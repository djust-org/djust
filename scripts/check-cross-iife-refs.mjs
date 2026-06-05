#!/usr/bin/env node
/**
 * Cross-IIFE bare-reference static guard (#1706).
 *
 * The bug class (recurred 3×: #1676 keep-fnames → #1688/#1690 bare
 * applyPatches → #1689 dup)
 * --------------------------------------------------------------------
 * The djust client bundle is built by concatenating
 *     python/djust/static/djust/src/[0-9]*.js
 * in lexicographic order into one file. Most modules wrap their body in
 * their OWN inner IIFE — `(function () { … })()`. A name declared inside
 * module B's inner IIFE (or at module B's top level) and published ONLY
 * via `globalThis.djust.<name> = <name>` is NOT in scope for module A's
 * inner IIFE.
 *
 * If module A references that name with a BARE identifier
 * (`applyPatches(...)`, `typeof applyPatches === "function"`,
 * `djust._x = applyPatches`), it "works" unminified only because terser
 * isn't involved; under `terser --compress --mangle` the bare reference
 * is broken / renamed inconsistently and throws
 * `ReferenceError: <name> is not defined` in client.min.js — a
 * production-only crash (`scripts/build-client.sh:42-49`). The correct
 * form reads the published alias: `globalThis.djust.<name>`.
 *
 * Per-symbol fixes + per-symbol regression tests
 * (`tests/js/min_bundle_applypatches_1676.test.js`,
 * `tests/js/min_bundle_applypatches_1688.test.js`) don't retire the
 * CLASS. This check does.
 *
 * What this check flags
 * ---------------------
 * For the set P of names published via `(globalThis|window|djust).djust.X = X`
 * where X is a bare identifier resolving to a function declared in some
 * module B, this check flags every BARE identifier reference to X that falls
 * OUTSIDE X's declaration's lexical scope barrier (its enclosing IIFE /
 * function body, or the double-load-guard `else {}` block) — i.e. a
 * reference that resolves only by luck unminified and throws under terser.
 * Such references must read the published alias `globalThis.djust.X`.
 *
 * Scope coverage (#1706 + #1716)
 * ------------------------------
 * The check models the actual bundle scope structure (see "Lexical
 * scope-barrier spans" below) and covers BOTH dangerous shapes:
 *   - GUARD-BLOCK → TOP-LEVEL (#1676/#1688): a function declared inside the
 *     double-load-guard `else {}` block (modules 00–20, block-scoped)
 *     referenced bare from bundle top level.
 *   - TOP-LEVEL → TOP-LEVEL (#1716): a function declared inside module A's
 *     own inner IIFE (modules 22–51) referenced bare from a DIFFERENT
 *     top-level module's IIFE. 10 of 58 published functions are
 *     top-level-declared and were gap-exposed before #1716 closed this.
 *
 * It is conservative by design (false-positive control):
 *   - Browser/host globals (`window`, `document`, `console`,
 *     `queueMicrotask`, `MutationObserver`, timers, `CustomEvent`, …) are
 *     inherently excluded — they are never in P (P only ever contains
 *     djust-published function names).
 *   - `djust.X` / `globalThis.djust.X` MEMBER ACCESS is the correct form
 *     and is never flagged (it's a MemberExpression, not a bare ref).
 *   - A function referenced bare from the SAME module it's declared in is
 *     never flagged, and a name locally re-bound in the referring module is
 *     excluded.
 *   - A PROGRAM-SCOPE function declaration (declared OUTSIDE any IIFE and
 *     outside the guard block — e.g. `maybeDeferRemoval` in 42-dj-remove.js)
 *     is a true global, visible everywhere even minified, so it has no
 *     barrier and a bare reference to it is NOT flagged.
 *
 * Documented limitation: a bare cross-IIFE reference to a NON-published
 * inner-IIFE function is not flagged by this check (such a reference is a
 * plain ReferenceError caught at load by existing JS tests). The
 * published-top-level gap that #1716 cited is now CLOSED; the only remaining
 * limitation is fully-general analysis of NON-published inner-IIFE
 * cross-references, a possible follow-up.
 *
 * Source dir is configurable via env var `BUNDLE_SRC_DIR` for testing
 * (mirrors `scripts/check-bundle-init-order.mjs`).
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
// 1. Build the in-memory bundle in lex order + a bundle-line → module map.
//    (Copy-faithful to check-bundle-init-order.mjs's buildBundle/mapLine —
//    the foundation module runs main() on import, so it cannot be imported
//    for its helpers; the duplication is small and pinned by the real-tree
//    vitest test.)
// ---------------------------------------------------------------------------
function buildBundle() {
    if (!fs.existsSync(SRC_DIR)) {
        console.error(`ERROR: source dir not found: ${SRC_DIR}`);
        process.exit(2);
    }
    const files = fs
        .readdirSync(SRC_DIR)
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

function moduleIndexOf(node, offsets) {
    const line = node.loc && node.loc.start ? node.loc.start.line : 0;
    return mapLine(line, offsets).fileIndex;
}

// ---------------------------------------------------------------------------
// 2. Generic recursive AST walk + binding-name collection.
// ---------------------------------------------------------------------------
const SKIP_KEYS = new Set(['type', 'loc', 'start', 'end', 'range']);

function walk(node, visitor) {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) {
        for (const item of node) walk(item, visitor);
        return;
    }
    if (typeof node.type !== 'string') return;

    const action = visitor(node);
    if (action === 'skip') return;

    for (const key of Object.keys(node)) {
        if (SKIP_KEYS.has(key)) continue;
        const child = node[key];
        if (child && typeof child === 'object') walk(child, visitor);
    }
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

// ---------------------------------------------------------------------------
// 2b. Lexical scope-barrier spans (#1706 + #1716).
//
// A function-name reference resolves at runtime by walking outward from the
// reference's lexical scope through every ENCLOSING scope to the program
// (global) scope. A bare reference to function X is in scope iff X's
// declaration sits in a scope that lexically CONTAINS the reference.
//
// Two scope barriers matter for the djust bundle, and BOTH are just line
// spans a declaration may sit inside:
//
//   (a) Per-module inner IIFE (`(function () { … })()`). Modules 22–51 each
//       wrap their body in their own IIFE. `function foo() {}` declared
//       inside module 22's IIFE is NOT visible as a bare name inside module
//       23's IIFE — each IIFE is a separate function scope. (#1716)
//
//   (b) The double-load-guard `else {}` block. 00-namespace.js opens
//       `if (window._djustClientLoaded) { … } else { … }` and
//       21-guard-close.js closes the `else {}`. Modules 00–20 sit INSIDE
//       that block (block scope); modules 22–51 sit at the program's true
//       top level, OUTSIDE it. `function foo() {}` declared in the `else {}`
//       block is block-scoped and NOT visible at program top level. (#1706)
//
// THE BARRIER of a declaration is the INNERMOST enclosing scope span that is
// narrower than the program — the nearest enclosing function/IIFE body if
// any, else the guard `else {}` block if the declaration sits directly in
// it, else NONE (program scope). A program-scope declaration (e.g.
// `maybeDeferRemoval` in 42-dj-remove.js, declared OUTSIDE any IIFE) is a
// true global: visible everywhere, even terser-minified — so it has no
// barrier and is never flagged.
//
// A bare reference to a published function X is out of scope (and thus a
// #1676/#1688/#1716 ReferenceError-under-minify risk) iff X has a barrier
// span AND the reference's line falls OUTSIDE that span. The correct form
// always reads the published alias `globalThis.djust.X`.
// ---------------------------------------------------------------------------
function findGuardElseSpan(ast) {
    let span = null;
    walk(ast, (node) => {
        if (span) return 'skip';
        if (node.type === 'IfStatement' && node.test &&
            node.test.type === 'MemberExpression' && !node.test.computed &&
            node.test.object && node.test.object.type === 'Identifier' &&
            node.test.object.name === 'window' &&
            node.test.property && node.test.property.type === 'Identifier' &&
            node.test.property.name === '_djustClientLoaded' &&
            node.alternate && node.alternate.type === 'BlockStatement' &&
            node.alternate.loc) {
            span = {
                start: node.alternate.loc.start.line,
                end: node.alternate.loc.end.line,
            };
        }
    });
    return span;
}

function lineOf(node) {
    return node && node.loc && node.loc.start ? node.loc.start.line : 0;
}

function withinSpan(line, span) {
    return !!span && line >= span.start && line <= span.end;
}

// ---------------------------------------------------------------------------
// 3. Function table: function-name -> declaring module index.
//    (Mirrors check-bundle-init-order.mjs's buildFunctionTable shape.)
// ---------------------------------------------------------------------------
function memberRightmostName(expr) {
    if (!expr) return null;
    if (expr.type === 'MemberExpression') {
        if (expr.computed) return null;
        if (expr.property && expr.property.type === 'Identifier') return expr.property.name;
    }
    return null;
}

function buildFunctionTable(ast, offsets, guardSpan) {
    // name -> { moduleIndex, barrierSpan, bundleLine } (first definition wins)
    //
    // barrierSpan is the innermost enclosing scope span the declaration sits
    // inside, narrower than the program (see "Lexical scope-barrier spans"):
    //   - the nearest ENCLOSING function/IIFE body's line span, if any;
    //   - else the guard `else {}` block span, if the declaration sits
    //     directly inside it (no intervening function scope);
    //   - else null (program scope — visible everywhere, never flagged).
    //
    // We track the enclosing-function-scope stack with a custom walk so each
    // declaration knows its nearest function-scope barrier. The guard block
    // is NOT a function scope, so it never enters the stack; it's consulted
    // only when the function-scope stack is empty.
    const fnTable = new Map();
    const fnScopeStack = [];  // line spans of enclosing function bodies

    function barrierFor(node) {
        // Nearest enclosing function scope wins; else the guard block iff the
        // declaration is directly inside it; else program scope (null).
        if (fnScopeStack.length > 0) {
            return fnScopeStack[fnScopeStack.length - 1];
        }
        if (guardSpan && withinSpan(lineOf(node), guardSpan)) {
            return guardSpan;
        }
        return null;
    }

    function addFn(name, node) {
        if (!name) return;
        if (fnTable.has(name)) return;
        fnTable.set(name, {
            moduleIndex: moduleIndexOf(node, offsets),
            barrierSpan: barrierFor(node),
            bundleLine: lineOf(node),
        });
    }

    function visit(node) {
        if (!node || typeof node !== 'object') return;
        if (Array.isArray(node)) {
            for (const item of node) visit(item);
            return;
        }
        if (typeof node.type !== 'string') return;

        // Record declarations (using the CURRENT enclosing scope as barrier)
        // BEFORE descending into any function body those declarations open.
        if (node.type === 'FunctionDeclaration' && node.id) {
            addFn(node.id.name, node);
        } else if (node.type === 'VariableDeclarator' && node.init &&
                   (node.init.type === 'FunctionExpression' ||
                    node.init.type === 'ArrowFunctionExpression') &&
                   node.id && node.id.type === 'Identifier') {
            addFn(node.id.name, node.init);
        } else if (node.type === 'AssignmentExpression' && node.operator === '=' &&
                   (node.right.type === 'FunctionExpression' ||
                    node.right.type === 'ArrowFunctionExpression')) {
            if (node.left.type === 'Identifier') {
                addFn(node.left.name, node.right);
            } else if (node.left.type === 'MemberExpression') {
                const name = memberRightmostName(node.left);
                if (name) addFn(name, node.right);
            }
        }

        const isFnScope = node.type === 'FunctionDeclaration' ||
            node.type === 'FunctionExpression' ||
            node.type === 'ArrowFunctionExpression';
        let pushed = false;
        if (isFnScope && node.body && node.body.loc) {
            fnScopeStack.push({
                start: node.body.loc.start.line,
                end: node.body.loc.end.line,
            });
            pushed = true;
        }
        for (const key of Object.keys(node)) {
            if (SKIP_KEYS.has(key)) continue;
            const child = node[key];
            if (child && typeof child === 'object') visit(child);
        }
        if (pushed) fnScopeStack.pop();
    }

    visit(ast);
    return fnTable;
}

// ---------------------------------------------------------------------------
// 4. Published-name set P.
//
// An assignment `(globalThis|window|djust).…djust.X = X` whose RHS is a
// bare Identifier resolving to a function in the table publishes X. We
// record P[X] = declaring module index (from the function table — the
// module that actually OWNS the symbol, not necessarily the publish site).
// ---------------------------------------------------------------------------
function isDjustNamespaceMember(memberExpr) {
    // Match `globalThis.djust.X`, `window.djust.X`, `self.djust.X`, or bare
    // `djust.X`. Returns the published prop name X, else null.
    if (!memberExpr || memberExpr.type !== 'MemberExpression' || memberExpr.computed) {
        return null;
    }
    if (!memberExpr.property || memberExpr.property.type !== 'Identifier') return null;
    const propName = memberExpr.property.name;
    const obj = memberExpr.object;
    // obj must be `djust` (Identifier) or `<root>.djust` (MemberExpression).
    if (obj && obj.type === 'Identifier' && obj.name === 'djust') {
        return propName;
    }
    if (obj && obj.type === 'MemberExpression' && !obj.computed &&
        obj.property && obj.property.type === 'Identifier' && obj.property.name === 'djust') {
        const root = obj.object;
        if (root && root.type === 'Identifier' &&
            (root.name === 'globalThis' || root.name === 'window' || root.name === 'self')) {
            return propName;
        }
    }
    return null;
}

function buildPublishedSet(ast, fnTable) {
    // X -> { moduleIndex, inGuard, bundleLine } (the declaring function)
    const published = new Map();
    walk(ast, (node) => {
        if (node.type === 'AssignmentExpression' && node.operator === '=' &&
            node.left.type === 'MemberExpression' &&
            node.right.type === 'Identifier') {
            const propName = isDjustNamespaceMember(node.left);
            if (propName !== null) {
                const rhsName = node.right.name;
                // Only treat as a dangerous-publish if the RHS bare symbol
                // resolves to a function in the bundle (the exact
                // `djust.applyPatches = applyPatches` shape). This excludes
                // value publications like `djust.apiPrefix = prefix`.
                if (fnTable.has(rhsName) && !published.has(rhsName)) {
                    published.set(rhsName, fnTable.get(rhsName));
                }
            }
        }
    });
    return published;
}

// ---------------------------------------------------------------------------
// 5. Per-module binding sets — every name bound anywhere in a module's AST
//    subtree (function decls, var/let/const, params, catch params, classes).
//    A name in bindings(A) is in scope for A and never flagged.
// ---------------------------------------------------------------------------
function buildModuleBindings(ast, offsets, numModules) {
    const bindings = [];
    for (let i = 0; i < numModules; i++) bindings.push(new Set());

    function bind(name, node) {
        if (!name) return;
        bindings[moduleIndexOf(node, offsets)].add(name);
    }

    walk(ast, (node) => {
        switch (node.type) {
            case 'FunctionDeclaration':
                if (node.id) bind(node.id.name, node.id);
                for (const p of node.params || []) {
                    const names = new Set();
                    collectPatternNames(p, names);
                    for (const n of names) bind(n, p);
                }
                break;
            case 'FunctionExpression':
            case 'ArrowFunctionExpression':
                if (node.id) bind(node.id.name, node.id);
                for (const p of node.params || []) {
                    const names = new Set();
                    collectPatternNames(p, names);
                    for (const n of names) bind(n, p);
                }
                break;
            case 'ClassDeclaration':
                if (node.id) bind(node.id.name, node.id);
                break;
            case 'VariableDeclarator': {
                const names = new Set();
                collectPatternNames(node.id, names);
                for (const n of names) bind(n, node.id);
                break;
            }
            case 'CatchClause':
                if (node.param) {
                    const names = new Set();
                    collectPatternNames(node.param, names);
                    for (const n of names) bind(n, node.param);
                }
                break;
        }
    });
    return bindings;
}

// ---------------------------------------------------------------------------
// 6. Bare-reference scan. Find every bare Identifier read/call/typeof that
//    is a published cross-module name not bound in its own module.
// ---------------------------------------------------------------------------
function findBareCrossIifeRefs({ ast, offsets, published, bindings }) {
    const violations = [];
    const seen = new Set();

    function flag(name, node) {
        const decl = published.get(name);            // { moduleIndex, barrierSpan, bundleLine }
        const useModule = moduleIndexOf(node, offsets);
        if (useModule === decl.moduleIndex) return;  // same module — in scope
        if (bindings[useModule].has(name)) return;   // bound locally — in scope

        // THE LOAD-BEARING SCOPE TEST (#1706 + #1716): the declaration sits
        // inside a lexical scope BARRIER — either the double-load-guard
        // `else {}` block (modules 00–20, block-scoped) or its own module's
        // inner IIFE (modules 22–51, function-scoped). A reference is out of
        // scope iff it falls OUTSIDE that barrier span. Cases:
        //   - decl in guard block, ref outside it (program top level): the
        //     original #1676/#1688 class — flagged.
        //   - decl in module A's IIFE, ref in module B (its own IIFE, or any
        //     position outside A's IIFE span): the #1716 top-level↔top-level
        //     gap — flagged.
        //   - decl + ref in the SAME barrier span (shared scope): in scope —
        //     NOT flagged (the bulk of legit cross-module calls).
        //   - decl at PROGRAM scope (no barrier, e.g. maybeDeferRemoval in
        //     42-dj-remove.js): a true global, visible everywhere even
        //     minified — NOT flagged.
        const refLine = lineOf(node);
        if (!decl.barrierSpan) return;                 // program-scope decl
        if (withinSpan(refLine, decl.barrierSpan)) return;  // ref shares scope

        const m = mapLine(node.loc.start.line, offsets);
        const key = `${name}@${m.file}:${m.fileLine}`;
        if (seen.has(key)) return;
        seen.add(key);
        violations.push({
            name,
            use: m,
            declModule: offsets[decl.moduleIndex] ? offsets[decl.moduleIndex].relPath : `#${decl.moduleIndex}`,
        });
    }

    // Custom walk: we must distinguish bare Identifier REFERENCES from
    // binding targets and member-property positions.
    function visit(node) {
        if (!node || typeof node !== 'object') return;
        if (Array.isArray(node)) {
            for (const item of node) visit(item);
            return;
        }
        if (typeof node.type !== 'string') return;

        switch (node.type) {
            case 'Identifier':
                if (published.has(node.name)) flag(node.name, node);
                return;
            case 'MemberExpression':
                // Walk object (a ref); skip non-computed property (it's
                // `obj.X` member access — the CORRECT form). Walk computed
                // property (`obj[X]` — X is a real ref).
                visit(node.object);
                if (node.computed) visit(node.property);
                return;
            case 'Property':
                // `{ X: val }` — non-computed key is not a ref; value is.
                if (node.computed) visit(node.key);
                visit(node.value);
                return;
            case 'MethodDefinition':
            case 'PropertyDefinition':
                if (node.computed) visit(node.key);
                if (node.value) visit(node.value);
                return;
            case 'VariableDeclarator':
                // id is a binding target (skip); init is a ref context.
                if (node.id && node.id.type !== 'Identifier') {
                    // Destructuring defaults can contain refs.
                    visitPatternDefaults(node.id);
                }
                if (node.init) visit(node.init);
                return;
            case 'AssignmentExpression':
                // LHS bare Identifier is a write target (skip recording the
                // name itself), but member-expr LHS objects are reads.
                if (node.left.type === 'MemberExpression') {
                    visit(node.left);
                } else if (node.left.type !== 'Identifier') {
                    visit(node.left);
                }
                visit(node.right);
                return;
            case 'FunctionDeclaration':
            case 'FunctionExpression':
            case 'ArrowFunctionExpression':
                // id + params are bindings; default values in params are refs.
                for (const p of node.params || []) visitPatternDefaults(p);
                visit(node.body);
                return;
            case 'LabeledStatement':
                visit(node.body);
                return;
            case 'BreakStatement':
            case 'ContinueStatement':
                return;
            default:
                break;
        }

        for (const key of Object.keys(node)) {
            if (SKIP_KEYS.has(key)) continue;
            const child = node[key];
            if (child && typeof child === 'object') visit(child);
        }
    }

    function visitPatternDefaults(pattern) {
        if (!pattern || typeof pattern !== 'object') return;
        if (pattern.type === 'AssignmentPattern') {
            visit(pattern.right);
            visitPatternDefaults(pattern.left);
        } else if (pattern.type === 'ObjectPattern') {
            for (const prop of pattern.properties) {
                if (prop.type === 'RestElement') visitPatternDefaults(prop.argument);
                else {
                    if (prop.computed) visit(prop.key);
                    visitPatternDefaults(prop.value);
                }
            }
        } else if (pattern.type === 'ArrayPattern') {
            for (const el of pattern.elements) if (el) visitPatternDefaults(el);
        } else if (pattern.type === 'RestElement') {
            visitPatternDefaults(pattern.argument);
        }
    }

    visit(ast);
    return violations;
}

// ---------------------------------------------------------------------------
// 7. Orchestration.
// ---------------------------------------------------------------------------
export function analyze() {
    const { bundle, offsets } = buildBundle();
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
    const numModules = new Set(offsets.map((o) => o.fileIndex)).size;
    const guardSpan = findGuardElseSpan(ast);
    const fnTable = buildFunctionTable(ast, offsets, guardSpan);
    const published = buildPublishedSet(ast, fnTable);
    const bindings = buildModuleBindings(ast, offsets, numModules);
    const violations = findBareCrossIifeRefs({ ast, offsets, published, bindings });
    return { numModules, published, violations };
}

function main() {
    const { numModules, published, violations } = analyze();

    if (violations.length === 0) {
        console.log(
            `OK: cross-IIFE bare-reference check clean across ${numModules} modules ` +
            `(${published.size} djust-published function(s) tracked).`
        );
        process.exit(0);
    }

    violations.sort((a, b) => {
        if (a.use.fileIndex !== b.use.fileIndex) return a.use.fileIndex - b.use.fileIndex;
        if (a.use.fileLine !== b.use.fileLine) return a.use.fileLine - b.use.fileLine;
        return a.name.localeCompare(b.name);
    });

    console.error(`FAIL: ${violations.length} bare cross-IIFE reference(s) found.`);
    console.error('');
    for (const v of violations) {
        console.error(`  BARE-CROSS-IIFE-REF: ${v.name}`);
        console.error(`    used at:   ${v.use.file}:${v.use.fileLine}  (bare identifier)`);
        console.error(`    declared:  ${v.declModule}  (published via globalThis.djust.${v.name})`);
    }
    console.error('');
    console.error('Remediation:');
    console.error('  Read the published alias instead of the bare symbol. Example:');
    console.error('    const fn = globalThis.djust && globalThis.djust.applyPatches;');
    console.error('    if (typeof fn === "function") { fn(...); }');
    console.error('');
    console.error('Why: each src module has its own inner IIFE; a bare reference to a');
    console.error('function declared in another module resolves only by luck in the');
    console.error('unminified bundle and throws ReferenceError under terser --mangle');
    console.error('(client.min.js). See #1676 / #1688 / #1706.');
    process.exit(1);
}

// Run as a CLI only when invoked directly (not when imported by tests).
const INVOKED_PATH = process.argv[1] ? path.resolve(process.argv[1]) : '';
if (INVOKED_PATH === fileURLToPath(import.meta.url)) {
    main();
}
