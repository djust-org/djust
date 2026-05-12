#!/usr/bin/env node
/**
 * Bundle init-order structural lint (#1372 + #1449).
 *
 * Background
 * ----------
 * The djust client bundle is built by:
 *     cat python/djust/static/djust/src/[0-9]*.js > client.js
 * Files are concatenated in lexicographic order. PR #1370 was a TDZ
 * regression: `let _activeHooks` declared in `19-hooks.js` was read at
 * top level by `djustInit()` in `14-init.js` (called synchronously
 * when `document.readyState !== 'loading'`). `let` is in TDZ until
 * its declaration line is REACHED — so module-N-declared lets used
 * top-level by module-M (where M < N) crash with
 * `Cannot access 'X' before initialization`.
 *
 * What this lint catches
 * ----------------------
 * 1) **Shallow (always on)**: direct top-level identifier reads of
 *    late-declared `let`/`const`. For every module-scope `let`/`const`
 *    declaration in the bundle, find every top-level (non-deferred)
 *    IDENTIFIER REFERENCE in any earlier module. Lex-order-before-
 *    declaration → flag.
 *
 * 2) **Deep / transitive (#1449)**: depth-N call-graph walker. When a
 *    top-level CallExpression resolves to a function in the bundle's
 *    function table, descend INTO that function's body in top-level
 *    mode. Identifiers found inside are effectively read at the
 *    ROOT-CALL bundle line (synchronous execution time), not at their
 *    lexical position. Decl-line > root-call-line → flag.
 *
 *    Cycle-guard via a `visited` set per root. Depth capped at
 *    `--max-depth=N` (default 8). `--shallow-only` (or `--max-depth=0`)
 *    reverts to v0.9.5 behavior.
 *
 * Deferral-site recognition (#1449)
 * ---------------------------------
 * The deep walker skips into function-literal / named-function args
 * of these call shapes — their bodies execute LATER, not at bundle
 * parse-time, so all declarations have settled by fire-time:
 *
 *   - DOM:        x.addEventListener(name, fn[, opts])
 *                 x.removeEventListener(name, fn[, opts])
 *   - Timers:     setTimeout(fn, ...), setInterval(fn, ...)
 *                 (also globalThis.* / window.*)
 *   - Microtask:  requestAnimationFrame(fn), queueMicrotask(fn),
 *                 requestIdleCallback(fn)
 *   - Promise:    p.then(fn[, errFn]), p.catch(fn), p.finally(fn)
 *   - Observer:   new MutationObserver(fn)
 *                 new IntersectionObserver(fn, opts)
 *                 new ResizeObserver(fn)  (any `new XxxObserver(...)`)
 *
 * Conservative default: any CallExpression we don't explicitly model
 * as "synchronous executor" does NOT have its function-literal args
 * descended into. We do NOT model `Array.prototype.forEach` etc. as
 * sync executors — the FP risk vs detection value isn't worth it.
 *
 * Implementation notes
 * --------------------
 * Individual source files are NOT valid standalone JS — there's a
 * `if (window._djustClientLoaded) { ... } else { ` block opened in
 * `00-namespace.js` and closed in `21-guard-close.js`. So we parse
 * the CONCATENATED bundle (in-memory, not the on-disk client.js)
 * and map AST line numbers back to source files via a line-offset
 * table.
 *
 * Source dir is configurable via env var `BUNDLE_SRC_DIR` for testing.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'acorn';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_DIR = path.resolve(SCRIPT_DIR, '..');
const DEFAULT_SRC_DIR = path.join(PROJECT_DIR, 'python/djust/static/djust/src');
const SRC_DIR = process.env.BUNDLE_SRC_DIR
    ? path.resolve(process.env.BUNDLE_SRC_DIR)
    : DEFAULT_SRC_DIR;

// ---------------------------------------------------------------------------
// CLI flags
// ---------------------------------------------------------------------------
function parseArgs(argv) {
    const opts = { maxDepth: 8, shallowOnly: false };
    for (const a of argv) {
        if (a === '--shallow-only') opts.shallowOnly = true;
        else if (a.startsWith('--max-depth=')) {
            const n = parseInt(a.slice('--max-depth='.length), 10);
            if (Number.isFinite(n) && n >= 0) opts.maxDepth = n;
        }
    }
    if (opts.maxDepth === 0) opts.shallowOnly = true;
    return opts;
}

// ---------------------------------------------------------------------------
// 1. Discover source files and concatenate (in-memory) in lex order.
// ---------------------------------------------------------------------------
function buildBundle() {
    if (!fs.existsSync(SRC_DIR)) {
        console.error(`ERROR: source dir not found: ${SRC_DIR}`);
        process.exit(2);
    }
    const all = fs.readdirSync(SRC_DIR);
    const files = all
        .filter((f) => /^[0-9].*\.js$/.test(f))
        .sort();
    if (files.length === 0) {
        console.error(`ERROR: no source files in ${SRC_DIR}`);
        process.exit(2);
    }

    const offsets = [];
    let cumLines = 0;
    let bundle = '';
    for (let i = 0; i < files.length; i++) {
        const filePath = path.join(SRC_DIR, files[i]);
        const content = fs.readFileSync(filePath, 'utf8');
        offsets.push({
            file: filePath,
            relPath: path.relative(PROJECT_DIR, filePath),
            startLine: cumLines + 1,
            fileIndex: i,
        });
        bundle += content;
        const nl = (content.match(/\n/g) || []).length;
        cumLines += nl;
    }
    return { bundle, offsets };
}

function mapLine(bundleLine, offsets) {
    let lo = 0, hi = offsets.length - 1, ans = 0;
    while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (offsets[mid].startLine <= bundleLine) {
            ans = mid;
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    const entry = offsets[ans];
    return {
        file: entry.relPath,
        fileLine: bundleLine - entry.startLine + 1,
        fileIndex: entry.fileIndex,
    };
}

// ---------------------------------------------------------------------------
// 2. AST walk infrastructure (shared).
// ---------------------------------------------------------------------------

const DEFERRED_BODY_TYPES = new Set([
    'FunctionDeclaration',
    'FunctionExpression',
    'ArrowFunctionExpression',
]);

function isIIFE(node) {
    return (
        node &&
        node.type === 'CallExpression' &&
        node.callee &&
        (node.callee.type === 'FunctionExpression' ||
         node.callee.type === 'ArrowFunctionExpression')
    );
}

function collectPatternNames(pattern, out) {
    if (!pattern) return;
    switch (pattern.type) {
        case 'Identifier':
            out.add(pattern.name);
            return;
        case 'ObjectPattern':
            for (const prop of pattern.properties) {
                if (prop.type === 'RestElement') collectPatternNames(prop.argument, out);
                else collectPatternNames(prop.value, out);
            }
            return;
        case 'ArrayPattern':
            for (const el of pattern.elements) {
                if (el) collectPatternNames(el, out);
            }
            return;
        case 'RestElement':
            collectPatternNames(pattern.argument, out);
            return;
        case 'AssignmentPattern':
            collectPatternNames(pattern.left, out);
            return;
    }
}

/** Generic recursive walk; visitor returns 'skip' to stop descent. */
function walk(node, ctx, visitor) {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) {
        for (const item of node) walk(item, ctx, visitor);
        return;
    }
    if (typeof node.type !== 'string') return;

    const action = visitor(node, ctx);
    if (action === 'skip') return;

    if (DEFERRED_BODY_TYPES.has(node.type)) {
        const childCtx = { ...ctx, topLevel: false };
        walk(node.body, childCtx, visitor);
        return;
    }
    if (node.type === 'ClassBody') {
        for (const member of node.body) {
            if (member.type === 'MethodDefinition' || member.type === 'PropertyDefinition') {
                if (member.computed) walk(member.key, ctx, visitor);
                if (member.value) walk(member.value, { ...ctx, topLevel: false }, visitor);
            } else {
                walk(member, ctx, visitor);
            }
        }
        return;
    }

    for (const key of Object.keys(node)) {
        if (key === 'type' || key === 'loc' || key === 'start' || key === 'end' || key === 'range') continue;
        const child = node[key];
        if (child && typeof child === 'object') walk(child, ctx, visitor);
    }
}

// ---------------------------------------------------------------------------
// 3. Top-level statement detection (unfold the double-load guard).
// ---------------------------------------------------------------------------

function isDoubleLoadGuard(stmt) {
    if (stmt.type !== 'IfStatement') return false;
    const t = stmt.test;
    if (!t) return false;
    if (t.type === 'MemberExpression' &&
        t.object && t.object.type === 'Identifier' && t.object.name === 'window' &&
        t.property && t.property.type === 'Identifier' && t.property.name === '_djustClientLoaded') {
        return true;
    }
    return false;
}

function flattenTopLevel(body) {
    const out = [];
    for (const stmt of body) {
        if (isDoubleLoadGuard(stmt) && stmt.alternate && stmt.alternate.type === 'BlockStatement') {
            out.push({ type: 'ExpressionStatement', expression: stmt.test, loc: stmt.loc });
            for (const s of stmt.consequent.body || []) out.push(s);
            for (const s of stmt.alternate.body) out.push(s);
        } else {
            out.push(stmt);
        }
    }
    return out;
}

// ---------------------------------------------------------------------------
// 4. Deferral-site recognition.
//
// Returns the SET of argument indices that should be treated as deferred
// (callback / handler args), OR null if this call is NOT a deferral site.
// For NewExpression nodes, similar: any function-arg index is deferred.
// ---------------------------------------------------------------------------

const TIMER_GLOBALS = new Set(['setTimeout', 'setInterval', 'setImmediate']);
const MICROTASK_GLOBALS = new Set([
    'requestAnimationFrame',
    'queueMicrotask',
    'requestIdleCallback',
]);
const PROMISE_METHODS = new Set(['then', 'catch', 'finally']);
const EVENT_REG_METHODS = new Set(['addEventListener', 'removeEventListener']);

function calleeMemberPropName(callee) {
    if (callee.type !== 'MemberExpression') return null;
    if (callee.computed) return null;
    if (!callee.property || callee.property.type !== 'Identifier') return null;
    return callee.property.name;
}

function calleeIdentifierName(callee) {
    if (callee.type === 'Identifier') return callee.name;
    if (callee.type === 'MemberExpression' && !callee.computed) {
        // For globalThis.setTimeout / window.setTimeout — surface the prop name.
        const obj = callee.object;
        if (obj && obj.type === 'Identifier' &&
            (obj.name === 'globalThis' || obj.name === 'window' || obj.name === 'self')) {
            if (callee.property && callee.property.type === 'Identifier') {
                return callee.property.name;
            }
        }
    }
    return null;
}

/**
 * For a CallExpression node, return { deferredArgIndices: Set<number> } if
 * this is a known deferral-site, else null. Non-listed indices are walked
 * normally (in current mode); listed indices have their FunctionExpression /
 * ArrowFunctionExpression / Identifier (named-fn-ref) args skipped from
 * top-level descent.
 */
function getCallDeferralSpec(node) {
    if (node.type !== 'CallExpression') return null;
    const callee = node.callee;
    if (!callee) return null;

    // Member call: x.addEventListener / x.then / etc.
    const mp = calleeMemberPropName(callee);
    if (mp !== null) {
        if (EVENT_REG_METHODS.has(mp)) {
            // (eventName, handler [, opts])
            return { deferredArgIndices: new Set([1]) };
        }
        if (PROMISE_METHODS.has(mp)) {
            if (mp === 'then') return { deferredArgIndices: new Set([0, 1]) };
            return { deferredArgIndices: new Set([0]) };
        }
        // globalThis.setTimeout / window.setTimeout etc. — handled below
        // via calleeIdentifierName.
    }

    const idName = calleeIdentifierName(callee);
    if (idName !== null) {
        if (TIMER_GLOBALS.has(idName)) {
            return { deferredArgIndices: new Set([0]) };
        }
        if (MICROTASK_GLOBALS.has(idName)) {
            return { deferredArgIndices: new Set([0]) };
        }
    }
    return null;
}

/** new XxxObserver(fn, ...) — fn at arg[0] is deferred. */
function getNewDeferralSpec(node) {
    if (node.type !== 'NewExpression') return null;
    const callee = node.callee;
    if (!callee) return null;
    if (callee.type === 'Identifier' && /Observer$/.test(callee.name)) {
        return { deferredArgIndices: new Set([0]) };
    }
    return null;
}

function isFunctionLikeArg(arg) {
    if (!arg) return false;
    return (
        arg.type === 'FunctionExpression' ||
        arg.type === 'ArrowFunctionExpression' ||
        arg.type === 'Identifier'  // named function reference
    );
}

// ---------------------------------------------------------------------------
// 5. Build function table.
//
// Maps function-name -> { node, defBundleLine }. `node` is the
// FunctionDeclaration / FunctionExpression / ArrowFunctionExpression body
// owner. We collect:
//   - FunctionDeclaration:   function foo() {...}
//   - VariableDeclarator with init = FunctionExpression / ArrowFunctionExpression
//   - AssignmentExpression: Identifier = function / arrow
//   - AssignmentExpression: <member chain>.foo = function / arrow  (uses
//     the rightmost property name)
//
// Anonymous functions without a resolvable name are skipped.
// First definition wins (so later overrides are ignored — that's OK for
// init-order analysis; we're modeling parse-time call resolution).
// ---------------------------------------------------------------------------

function buildFunctionTable(ast) {
    const fnTable = new Map();

    function addFn(name, node) {
        if (!name) return;
        if (fnTable.has(name)) return;
        const line = (node.loc && node.loc.start && node.loc.start.line) || 0;
        fnTable.set(name, { node, defBundleLine: line });
    }

    function memberRightmostName(expr) {
        // For `a.b.c`, return `c`. For computed access, return null.
        if (!expr) return null;
        if (expr.type === 'MemberExpression') {
            if (expr.computed) return null;
            if (expr.property && expr.property.type === 'Identifier') return expr.property.name;
        }
        return null;
    }

    // Walk EVERYTHING (top-level and nested) — function definitions can sit
    // anywhere; we want the universe.
    walk(ast, { topLevel: true }, (node) => {
        if (node.type === 'FunctionDeclaration' && node.id) {
            addFn(node.id.name, node);
        } else if (node.type === 'VariableDeclarator' && node.init) {
            if (node.init.type === 'FunctionExpression' ||
                node.init.type === 'ArrowFunctionExpression') {
                if (node.id && node.id.type === 'Identifier') {
                    addFn(node.id.name, node.init);
                }
            }
        } else if (node.type === 'AssignmentExpression' &&
                   node.operator === '=' &&
                   (node.right.type === 'FunctionExpression' ||
                    node.right.type === 'ArrowFunctionExpression')) {
            if (node.left.type === 'Identifier') {
                addFn(node.left.name, node.right);
            } else if (node.left.type === 'MemberExpression') {
                const name = memberRightmostName(node.left);
                if (name) addFn(name, node.right);
            }
        }
    });

    return fnTable;
}

// ---------------------------------------------------------------------------
// 6. Pass 1: collect module-scope let/const declarations.
// ---------------------------------------------------------------------------

function collectDecls(topLevelStatements) {
    const decls = new Map();
    for (const stmt of topLevelStatements) {
        if (stmt.type === 'VariableDeclaration' &&
            (stmt.kind === 'let' || stmt.kind === 'const')) {
            for (const declarator of stmt.declarations) {
                const names = new Set();
                collectPatternNames(declarator.id, names);
                for (const name of names) {
                    if (!decls.has(name)) {
                        const line = declarator.id.loc
                            ? declarator.id.loc.start.line
                            : stmt.loc.start.line;
                        decls.set(name, { bundleLine: line });
                    }
                }
            }
        }
    }
    return decls;
}

// ---------------------------------------------------------------------------
// 7. Top-level walker for IDENTIFIER REFERENCES.
//
// Two modes:
//   shallow: identifier ref recorded at its own bundle line.
//   deep:    when we hit a CallExpression whose callee resolves in fnTable,
//            descend into that function's body with topLevel=true. The
//            effective use-line for any identifier read during the descent
//            is the ROOT-CALL bundle line (the first call into a known fn
//            from a top-level statement).
//
// We pass a `rootCallLine` through the descent. `null` => use the
// identifier's own lexical line (shallow / direct-read case).
// ---------------------------------------------------------------------------

function collectUses({ topLevelStatements, fnTable, opts }) {
    const uses = [];  // { name, bundleLine, callChain: [str] }
    const DEEP = !opts.shallowOnly;
    const MAX_DEPTH = opts.maxDepth;

    // Avoid double-recording: if multiple call paths reach the same
    // identifier site, we want the SHORTEST. Key by (name, lexicalLine).
    const seenUse = new Map();  // key -> {bundleLine, callChain}

    function recordUse(name, lexicalLine, rootCallLine, callChain) {
        const effectiveLine = rootCallLine !== null ? rootCallLine : lexicalLine;
        const key = `${name}@${lexicalLine}@${effectiveLine}`;
        const existing = seenUse.get(key);
        if (existing && existing.callChain.length <= callChain.length) return;
        seenUse.set(key, { name, bundleLine: effectiveLine, lexicalLine, callChain });
    }

    function visitorFactory({ rootCallLine, depth, visited, callChain }) {
        return (node, ctx) => {
            if (!ctx.topLevel) return;

            if (node.type === 'VariableDeclarator') {
                if (node.init) walk(node.init, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                return 'skip';
            }
            if (node.type === 'AssignmentExpression') {
                // Treat LHS Identifier as a BINDING write (don't record), but
                // walk member-expr LHS objects (those are reads).
                if (node.left.type === 'MemberExpression') {
                    walk(node.left, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                } else if (node.left.type !== 'Identifier') {
                    walk(node.left, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                }
                walk(node.right, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                return 'skip';
            }
            if (node.type === 'MemberExpression') {
                walk(node.object, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                if (node.computed) walk(node.property, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                return 'skip';
            }
            if (node.type === 'Property' && !node.computed) {
                walk(node.value, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                return 'skip';
            }
            if (node.type === 'LabeledStatement') {
                walk(node.body, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                return 'skip';
            }
            if (node.type === 'BreakStatement' || node.type === 'ContinueStatement') {
                return 'skip';
            }
            if (node.type === 'UnaryExpression' && node.operator === 'typeof' &&
                node.argument && node.argument.type === 'Identifier') {
                // `typeof X` does NOT trigger TDZ for undeclared, BUT it DOES
                // for `let`/`const` in TDZ. Still — these patterns are
                // typically TDZ-safe-by-design guards. Record at LEXICAL line
                // only (no transitive promotion via rootCallLine). This means
                // a `typeof X` guard inside a function body called from
                // top-level won't be flagged (the rootCallLine is somewhere
                // earlier, but `typeof` is the universal "is it defined yet"
                // check — false positives here outweigh the rare real-TDZ
                // case). For shallow direct reads we still record.
                if (rootCallLine === null) {
                    const line = node.argument.loc.start.line;
                    recordUse(node.argument.name, line, null, callChain);
                }
                return 'skip';
            }
            if (isIIFE(node)) {
                const callee = node.callee;
                for (const arg of node.arguments) {
                    walk(arg, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                }
                for (const p of (callee.params || [])) {
                    if (p.type === 'AssignmentPattern' && p.right) {
                        walk(p.right, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                    }
                }
                if (callee.body) {
                    walk(callee.body, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                }
                return 'skip';
            }
            if (node.type === 'CallExpression') {
                // 1) deferral-site? If so, skip function-like args and walk
                //    others normally.
                const spec = getCallDeferralSpec(node);
                // Walk callee (it might be `foo.bar.baz()` where bar is a ref).
                walk(node.callee, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));

                for (let i = 0; i < node.arguments.length; i++) {
                    const arg = node.arguments[i];
                    if (spec && spec.deferredArgIndices.has(i) && isFunctionLikeArg(arg)) {
                        // Skip — deferred.
                        continue;
                    }
                    walk(arg, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                }

                // 2) DEEP descent: if callee is a known-function reference,
                //    walk its body in top-level mode at depth+1.
                if (DEEP && depth < MAX_DEPTH) {
                    const calleeName = (node.callee.type === 'Identifier')
                        ? node.callee.name
                        : null;
                    if (calleeName && fnTable.has(calleeName) && !visited.has(calleeName)) {
                        const fn = fnTable.get(calleeName);
                        const newVisited = new Set(visited);
                        newVisited.add(calleeName);
                        const rootLine = rootCallLine !== null
                            ? rootCallLine
                            : node.loc.start.line;
                        const newChain = callChain.concat([
                            `${calleeName}()`,
                        ]);
                        // Walk default-value expressions in params (rare,
                        // but they DO run at call time).
                        for (const p of (fn.node.params || [])) {
                            if (p.type === 'AssignmentPattern' && p.right) {
                                walk(p.right, { topLevel: true }, visitorFactory({
                                    rootCallLine: rootLine,
                                    depth: depth + 1,
                                    visited: newVisited,
                                    callChain: newChain,
                                }));
                            }
                        }
                        if (fn.node.body) {
                            walk(fn.node.body, { topLevel: true }, visitorFactory({
                                rootCallLine: rootLine,
                                depth: depth + 1,
                                visited: newVisited,
                                callChain: newChain,
                            }));
                        }
                    }
                }
                return 'skip';
            }
            if (node.type === 'NewExpression') {
                const spec = getNewDeferralSpec(node);
                walk(node.callee, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                for (let i = 0; i < node.arguments.length; i++) {
                    const arg = node.arguments[i];
                    if (spec && spec.deferredArgIndices.has(i) && isFunctionLikeArg(arg)) {
                        continue;
                    }
                    walk(arg, ctx, visitorFactory({ rootCallLine, depth, visited, callChain }));
                }
                return 'skip';
            }
            if (node.type === 'Identifier') {
                recordUse(node.name, node.loc.start.line, rootCallLine, callChain);
                return 'skip';
            }
            if (node.type === 'FunctionDeclaration' || node.type === 'ClassDeclaration') {
                // Default-walker skip via DEFERRED_BODY_TYPES handles the
                // body. Decl name is not a ref.
                return;
            }
        };
    }

    for (const stmt of topLevelStatements) {
        walk(stmt, { topLevel: true }, visitorFactory({
            rootCallLine: null,
            depth: 0,
            visited: new Set(),
            callChain: [],
        }));
    }

    for (const v of seenUse.values()) uses.push(v);
    return uses;
}

// ---------------------------------------------------------------------------
// 8. Analyze + cross-module ordering check.
// ---------------------------------------------------------------------------

function analyzeBundle(bundle, opts) {
    let ast;
    try {
        ast = parse(bundle, {
            ecmaVersion: 'latest',
            sourceType: 'script',
            locations: true,
            allowReturnOutsideFunction: true,
        });
    } catch (e) {
        console.error(`ERROR: bundle parse failed: ${e.message}`);
        if (e.loc) console.error(`  at bundle line ${e.loc.line}, column ${e.loc.column}`);
        process.exit(2);
    }

    const topLevelStatements = flattenTopLevel(ast.body);
    const decls = collectDecls(topLevelStatements);
    const fnTable = buildFunctionTable(ast);
    const uses = collectUses({ topLevelStatements, fnTable, opts });

    return { decls, uses };
}

function main() {
    const opts = parseArgs(process.argv.slice(2));
    const { bundle, offsets } = buildBundle();
    const { decls, uses } = analyzeBundle(bundle, opts);

    const violations = [];
    for (const u of uses) {
        const decl = decls.get(u.name);
        if (!decl) continue;
        if (u.bundleLine < decl.bundleLine) {
            const useMap = mapLine(u.lexicalLine, offsets);
            const declMap = mapLine(decl.bundleLine, offsets);
            const effMap = mapLine(u.bundleLine, offsets);
            violations.push({
                name: u.name,
                use: useMap,                  // where the identifier lexically appears
                useBundleLine: u.lexicalLine,
                effective: effMap,            // where the synchronous use effectively executes
                effectiveBundleLine: u.bundleLine,
                decl: declMap,
                declBundleLine: decl.bundleLine,
                callChain: u.callChain,
                transitive: u.callChain.length > 0,
            });
        }
    }

    // Dedup: a single (name, lexicalLine, effectiveLine) is one violation.
    const seen = new Set();
    const uniq = [];
    for (const v of violations) {
        const key = `${v.name}@${v.useBundleLine}@${v.effectiveBundleLine}`;
        if (seen.has(key)) continue;
        seen.add(key);
        uniq.push(v);
    }

    uniq.sort((a, b) => {
        if (a.use.fileIndex !== b.use.fileIndex) return a.use.fileIndex - b.use.fileIndex;
        if (a.use.fileLine !== b.use.fileLine) return a.use.fileLine - b.use.fileLine;
        return a.name.localeCompare(b.name);
    });

    if (uniq.length === 0) {
        const numModules = new Set(offsets.map((o) => o.fileIndex)).size;
        const mode = opts.shallowOnly
            ? 'shallow-only'
            : `depth ≤ ${opts.maxDepth}`;
        console.log(`OK: bundle init-order check clean across ${numModules} modules (${mode}).`);
        process.exit(0);
    }

    console.error(`FAIL: ${uniq.length} bundle init-order violation(s) found.`);
    console.error('');
    for (const v of uniq) {
        const label = v.transitive ? 'USE-BEFORE-DECL (transitive)' : 'USE-BEFORE-DECL';
        console.error(`  ${label}: ${v.name}`);
        console.error(`    declared:   ${v.decl.file}:${v.decl.fileLine}  (let/const)`);
        console.error(`    used at:    ${v.use.file}:${v.use.fileLine}  (top-level)`);
        if (v.transitive) {
            console.error(`    root call:  ${v.effective.file}:${v.effective.fileLine}`);
            console.error(`    call chain: ${v.callChain.join(' → ')}`);
        }
    }
    console.error('');
    console.error('Remediation options:');
    console.error('  1. Change `let X` / `const X` to `var X` if hoisted-undefined is acceptable');
    console.error('     (lazy-init pattern — see python/djust/static/djust/src/19-hooks.js for the canonical example).');
    console.error('  2. Move the declaration into a module that lex-orders BEFORE the use site.');
    console.error('  3. Move the top-level use into a deferred function (called from djustInit /');
    console.error('     DOMContentLoaded / etc. — anything that runs AFTER the bundle finishes parsing).');
    console.error('');
    console.error('Reference: PR #1370 / #1371 / #1449 (TDZ regression class + depth-N walker).');
    process.exit(1);
}

main();
