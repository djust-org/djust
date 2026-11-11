/**
 * #3963: `dj-disable-with` put the button's pre-submit text back AFTER the
 * response's patches, overwriting text (and `disabled`) the same response had
 * just rendered. Real sequence, over the HTTP fallback: a real `submit` swaps
 * the label to the disable text and disables the button; the response arrives
 * with patches / HTML that morph the SAME button in place.
 *
 * Ordering invariant asserted: the response has the last word. After a
 * response, every property it rendered (text, name, disabled) is what the
 * server said, and a property it left alone is back to its pre-submit value.
 * The restore runs BEFORE the patches, so the DOM they are applied to is the
 * server's baseline, not the transient "Saving" one (a SetText is only emitted
 * for text that changed, so a patch applied to "Saving" would never be sent
 * for an unchanged label).
 */
import { describe, expect, it } from 'vitest';
import { legacy, setup, submit } from './_form_events_harness.js';

// Whitespace between elements, as a server-rendered page has it. Significant
// child paths (the patch addressing): root -> [p, form]; form -> [button];
// button -> [text].
const STEPS = `
    <p>Step 1 of 2</p>
    <form dj-submit="advance">
        <button type="submit" name="next" dj-disable-with="Saving">Next</button>
    </form>
`;
const PARAGRAPH = [0, 0];
const BUTTON = [1, 0];
const LABEL = [1, 0, 0];

const handlers = { advance: legacy() };

async function press(patches, extra = {}) {
    const t = setup(STEPS, handlers, () => ({ patches, ...extra }));
    const button = t.$('button');
    submit(t.win, t.$('form'), button);
    // Precondition: the transient state is visible before the response.
    expect([button.textContent, button.disabled]).toEqual(['Saving', true]);
    await t.settle();
    return { t, button };
}

describe('dj-disable-with does not overwrite what the response rendered (#3963)', () => {
    it('keeps the label the server just rendered ("Next" -> "Finish")', async () => {
        const { t, button } = await press([
            { type: 'SetText', path: PARAGRAPH, text: 'Step 2 of 2' },
            { type: 'SetAttr', path: BUTTON, key: 'name', value: 'finish' },
            { type: 'SetText', path: LABEL, text: 'Finish' },
        ]);
        expect(t.$('p').textContent).toBe('Step 2 of 2'); // the patches did land
        expect(button.getAttribute('name')).toBe('finish');
        expect(button.textContent).toBe('Finish');
        expect(button.disabled).toBe(false);
        expect(button.hasAttribute('data-djust-original-text')).toBe(false);
    });

    it('restores the pre-submit label when the response left it alone', async () => {
        const { button } = await press([]);
        expect(button.textContent).toBe('Next');
        expect(button.disabled).toBe(false);
        expect(button.hasAttribute('data-djust-original-text')).toBe(false);
    });

    it('restores it when the response changed only something else', async () => {
        const { t, button } = await press([{ type: 'SetText', path: PARAGRAPH, text: 'Step 2 of 2' }]);
        expect(t.$('p').textContent).toBe('Step 2 of 2');
        expect(button.textContent).toBe('Next');
        expect(button.disabled).toBe(false);
    });

    it('keeps a button the server rendered disabled (the restore must not re-enable it)', async () => {
        const { button } = await press([{ type: 'SetAttr', path: BUTTON, key: 'disabled', value: '' }]);
        expect(button.disabled).toBe(true);
        expect(button.textContent).toBe('Next');
    });

    it('full-HTML responses (the morph path) have the last word too', async () => {
        const { t } = await press(undefined, {
            html: `<div dj-view="app.Page" dj-root>
                <p>Step 2 of 2</p>
                <form dj-submit="advance">
                    <button type="submit" name="finish" dj-disable-with="Saving">Finish</button>
                </form>
            </div>`,
            patches: undefined,
        });
        expect(t.$('p').textContent).toBe('Step 2 of 2');
        expect(t.$('button').textContent).toBe('Finish');
        expect(t.$('button').disabled).toBe(false);
    });
});
