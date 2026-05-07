#!/usr/bin/env node
/**
 * Bundle init-order structural lint (#1372).
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
 * For every module-scope `let`/`const` declaration in the bundle,
 * find every top-level (non-deferred) reference. If a reference site
 * lex-orders BEFORE the declaration site, that's a structural TDZ
 * bug. The runtime regression test
 * `tests/js/bundle-init-no-tdz.test.js` catches the SYMPTOM by
 * eval'ing the bundle in JSDOM; this lint catches the CLASS at
 * lint time.
 *
 * Implementation note
 * -------------------
 * Individual source files are NOT valid standalone JS — there's a
 * `if (window._djustClientLoaded) { ... } else { ` block opened in
 * `00-namespace.js` and closed in `21-guard-close.js`. So we parse
 * the CONCATENATED bundle (in-memory, not the on-disk client.js)
 * and map AST line numbers back to source files via a line-offset
 * table.
 *
 * Scope
 * -----
 * Top-level refs include: bare expression statements, IIFE bodies
 * (`(function(){...})()` and arrow-IIFE), `if`/`for`/`while` at the
 * top level. Refs INSIDE non-immediately-invoked function/method/arrow
 * bodies and class bodies are deferred and ignored — those run only
 * when the containing function is later called.
 *
 * Edge cases:
 *   - `var` decls can't TDZ (hoisted-undefined) — skipped.
 *   - `function` decls hoist — declarations are not tracked here.
 *   - IIFEs DO execute synchronously, so their body IS top-level
 *     for the purposes of this check.
 *   - Lets declared inside the double-load `else { }` block are
 *     considered MODULE-SCOPE (their TDZ window is the same — they
 *     are unreachable to top-level statements before the `else`
 *     opens, but the `else` opens at the very top, so we treat the
 *     contents as if they were module-scope).
 *
 * Implementation: we treat the bundle's top-level body PLUS the
 * body of any `IfStatement.alternate` whose test references
 * `_djustClientLoaded` as the same flat top-level scope.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'acorn';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_DIR = path.resolve(SCRIPT_DIR, '..');
const SRC_DIR = path.join(PROJECT_DIR, 'python/djust/static/djust/src');

// ---------------------------------------------------------------------------
// 1. Discover source files and concatenate (in-memory) in lex order.
//    Build a line-offset table to map bundle line back to (file, fileLine).
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

    // offsets[i] = { file, startLine } where startLine is the bundle line
    // (1-based) where this file's first line lives.
    const offsets = [];
    let cumLines = 0;
    let bundle = '';
    for (let i = 0; i < files.length; i++) {
        const filePath = path.join(SRC_DIR, files[i]);
        const content = fs.readFileSync(filePath, 'utf8');
        // Each `cat`-concatenation does NOT add a separator beyond the
        // file's own trailing newline. Mirror that exactly.
        offsets.push({
            file: filePath,
            relPath: path.relative(PROJECT_DIR, filePath),
            startLine: cumLines + 1,
            fileIndex: i,
        });
        bundle += content;
        // Count newlines in this file's content (including trailing).
        const nl = (content.match(/\n/g) || []).length;
        cumLines += nl;
    }
    return { bundle, offsets };
}

/**
 * Map a bundle line (1-based) to (file, fileLine, fileIndex).
 * Uses binary search over the offsets table.
 */
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
// 2. AST walk infrastructure.
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

/**
 * Walk children of `node` with given context, calling visitor.
 * Visitor may return 'skip' to short-circuit descent.
 */
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
// 3. Collect module-scope let/const decls and top-level uses.
//
// `topLevelStatements` is the FLAT list of statements considered to execute
// at bundle top level. We start with `ast.body`, then UNFOLD any
// `if (window._djustClientLoaded) { ... } else { <FLAT TOP> }` whose
// alternate body is a BlockStatement — we treat its statements as if they
// were directly at top level (because this `else` opens at module scope and
// contains the bulk of the bundle).
// ---------------------------------------------------------------------------

function isDoubleLoadGuard(stmt) {
    if (stmt.type !== 'IfStatement') return false;
    const t = stmt.test;
    if (!t) return false;
    // test is `window._djustClientLoaded`
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
            // Walk the if-test as a top-level expression (it does run).
            // We model the test as an ExpressionStatement so its identifier
            // refs are collected.
            out.push({ type: 'ExpressionStatement', expression: stmt.test, loc: stmt.loc });
            // Walk consequent statements at top level (they run when the
            // condition is true — but they're typically just a console.log).
            for (const s of stmt.consequent.body || []) out.push(s);
            // CRITICAL: unfold else-block's children as if at top level.
            for (const s of stmt.alternate.body) out.push(s);
        } else {
            out.push(stmt);
        }
    }
    return out;
}

function analyzeBundle(bundle) {
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

    // Pass 1: collect module-scope let/const declarations. Map name ->
    // bundle line of the declarator id.
    const decls = new Map();  // name -> { bundleLine }
    for (const stmt of topLevelStatements) {
        if (stmt.type === 'VariableDeclaration' && (stmt.kind === 'let' || stmt.kind === 'const')) {
            for (const declarator of stmt.declarations) {
                const names = new Set();
                collectPatternNames(declarator.id, names);
                for (const name of names) {
                    if (!decls.has(name)) {
                        const line = declarator.id.loc ? declarator.id.loc.start.line : stmt.loc.start.line;
                        decls.set(name, { bundleLine: line });
                    }
                }
            }
        }
    }

    // Pass 2: collect top-level Identifier references.
    const uses = [];  // { name, bundleLine }

    const visitor = (node, ctx) => {
        if (!ctx.topLevel) return;

        if (node.type === 'VariableDeclarator') {
            // id is binding; init is reference. Walk init only.
            if (node.init) walk(node.init, ctx, visitor);
            return 'skip';
        }
        if (node.type === 'AssignmentExpression') {
            walk(node.left, ctx, visitor);
            walk(node.right, ctx, visitor);
            return 'skip';
        }
        if (node.type === 'MemberExpression') {
            walk(node.object, ctx, visitor);
            if (node.computed) walk(node.property, ctx, visitor);
            return 'skip';
        }
        if (node.type === 'Property' && !node.computed) {
            // shorthand `{ foo }` — value === key === Identifier — that's a
            // real reference. acorn sets `shorthand: true`.
            if (node.shorthand) {
                walk(node.value, ctx, visitor);
            } else {
                walk(node.value, ctx, visitor);
            }
            return 'skip';
        }
        if (node.type === 'LabeledStatement') {
            walk(node.body, ctx, visitor);
            return 'skip';
        }
        if (node.type === 'BreakStatement' || node.type === 'ContinueStatement') {
            return 'skip';
        }
        if (isIIFE(node)) {
            // IIFE callee body executes NOW at top level. Walk args + body
            // in top-level mode. Skip the function-self-name (callee.id) —
            // it's a binding inside the IIFE only.
            for (const arg of node.arguments) walk(arg, ctx, visitor);
            const callee = node.callee;
            // Walk callee.params default-value expressions (rare). The
            // params themselves are bindings, not refs.
            for (const p of (callee.params || [])) {
                if (p.type === 'AssignmentPattern' && p.right) {
                    walk(p.right, ctx, visitor);
                }
            }
            if (callee.body) walk(callee.body, ctx, visitor);
            return 'skip';
        }
        if (node.type === 'Identifier') {
            uses.push({ name: node.name, bundleLine: node.loc.start.line });
            return 'skip';
        }
        if (node.type === 'FunctionDeclaration' || node.type === 'ClassDeclaration') {
            // Don't treat the decl name as a reference. Walk body via default
            // (which descends into FunctionDeclaration body with topLevel=false).
            return;
        }
    };

    for (const stmt of topLevelStatements) {
        walk(stmt, { topLevel: true }, visitor);
    }

    return { decls, uses };
}

// ---------------------------------------------------------------------------
// 4. Cross-module ordering check.
// ---------------------------------------------------------------------------

function main() {
    const { bundle, offsets } = buildBundle();
    const { decls, uses } = analyzeBundle(bundle);

    const violations = [];
    for (const u of uses) {
        const decl = decls.get(u.name);
        if (!decl) continue;
        if (u.bundleLine < decl.bundleLine) {
            const useMap = mapLine(u.bundleLine, offsets);
            const declMap = mapLine(decl.bundleLine, offsets);
            // Only flag CROSS-MODULE violations OR same-file backwards refs.
            // (Acorn would have caught a same-file forward ref in non-strict
            // mode? Actually no — a `let` referenced before its line in the
            // same file IS a real TDZ at runtime. Flag it.)
            violations.push({
                name: u.name,
                use: useMap,
                useBundleLine: u.bundleLine,
                decl: declMap,
                declBundleLine: decl.bundleLine,
            });
        }
    }

    // Dedup: a single Identifier may appear multiple times if walkers visit
    // it twice. Defensive set keyed on (name, useBundleLine).
    const seen = new Set();
    const uniq = [];
    for (const v of violations) {
        const key = `${v.name}@${v.useBundleLine}`;
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
        console.log(`OK: bundle init-order check clean across ${numModules} modules.`);
        process.exit(0);
    }

    console.error(`FAIL: ${uniq.length} bundle init-order violation(s) found.`);
    console.error('');
    for (const v of uniq) {
        console.error(`  USE-BEFORE-DECL: ${v.name}`);
        console.error(`    used at:    ${v.use.file}:${v.use.fileLine}  (top-level)`);
        console.error(`    declared:   ${v.decl.file}:${v.decl.fileLine}  (let/const)`);
    }
    console.error('');
    console.error('Remediation options:');
    console.error('  1. Change `let X` / `const X` to `var X` if hoisted-undefined is acceptable');
    console.error('     (lazy-init pattern — see python/djust/static/djust/src/19-hooks.js for the canonical example).');
    console.error('  2. Move the declaration into a module that lex-orders BEFORE the use site.');
    console.error('  3. Move the top-level use into a deferred function (called from djustInit /');
    console.error('     DOMContentLoaded / etc. — anything that runs AFTER the bundle finishes parsing).');
    console.error('');
    console.error('Reference: PR #1370 / #1371 (the original TDZ regression).');
    process.exit(1);
}

main();
