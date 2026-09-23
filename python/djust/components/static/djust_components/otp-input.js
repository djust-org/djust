/**
 * OTP Input — one digit per box, focus advances, the code fills the hidden input.
 *
 * `otp_input` renders its `.otp-digit` boxes and a hidden `.otp-hidden` input
 * that carries the component's `dj-change`. Nothing drove the boxes: typing
 * left one digit in the first box and the event never fired (#2975).
 *
 * Delegated from `document`, so there is no per-element state to lose when a
 * LiveView morph replaces the boxes, and no bind marker in the DOM (a morph
 * strips attributes it did not render).
 */
(function () {
  if (window.__djOtpInputBound) return;
  window.__djOtpInputBound = true;

  function parts(digit) {
    const root = digit.closest(".otp-input");
    if (!root) return null;
    return {
      boxes: Array.prototype.slice.call(root.querySelectorAll(".otp-digit")),
      hidden: root.querySelector(".otp-hidden"),
    };
  }

  function commit(p) {
    if (!p.hidden) return;
    const code = p.boxes
      .map(function (b) {
        return b.value;
      })
      .join("");
    if (code.length !== p.boxes.length || code === p.hidden.value) return;
    p.hidden.value = code;
    // The component's `dj-change` lives on the hidden input.
    p.hidden.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function fill(p, start, text) {
    const chars = text.replace(/\D/g, "").split("");
    let i = start;
    while (chars.length && i < p.boxes.length) {
      p.boxes[i].value = chars.shift();
      i += 1;
    }
    p.boxes[Math.min(i, p.boxes.length - 1)].focus();
    commit(p);
  }

  document.addEventListener("input", function (e) {
    const digit = e.target;
    if (!digit.classList || !digit.classList.contains("otp-digit")) return;
    const p = parts(digit);
    if (!p) return;
    const value = digit.value;
    digit.value = "";
    fill(p, p.boxes.indexOf(digit), value);
  });

  document.addEventListener("keydown", function (e) {
    const digit = e.target;
    if (!digit.classList || !digit.classList.contains("otp-digit")) return;
    const p = parts(digit);
    if (!p) return;
    const i = p.boxes.indexOf(digit);
    if (e.key === "Backspace" && !digit.value && i > 0) {
      p.boxes[i - 1].value = "";
      p.boxes[i - 1].focus();
      e.preventDefault();
    } else if (e.key === "ArrowLeft" && i > 0) {
      p.boxes[i - 1].focus();
    } else if (e.key === "ArrowRight" && i < p.boxes.length - 1) {
      p.boxes[i + 1].focus();
    }
  });

  document.addEventListener("paste", function (e) {
    const digit = e.target;
    if (!digit.classList || !digit.classList.contains("otp-digit")) return;
    const p = parts(digit);
    if (!p) return;
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData("text");
    fill(p, p.boxes.indexOf(digit), text);
  });
})();
