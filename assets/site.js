/* Easy Protein Kitchen: progressive enhancement only; every page works without JS. */
(() => {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finePointer = matchMedia("(hover: hover) and (pointer: fine)").matches;

  // 1. The hero plate stack fans out once on load (the single orchestrated moment).
  const stack = document.querySelector(".stack");
  if (stack && !reduce) {
    stack.classList.add("closed");
    void stack.offsetWidth;                 // commit the closed state before animating
    setTimeout(() => stack.classList.remove("closed"), 300);
  }

  // 2. Tilt follows the pointer on desktop (answers the person's action).
  function tilt(el, target, max) {
    if (!el || reduce || !finePointer) return;
    el.addEventListener("pointermove", (e) => {
      const r = el.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - 0.5;
      const y = (e.clientY - r.top) / r.height - 0.5;
      target.style.transform = `rotateY(${x * max}deg) rotateX(${-y * max}deg)`;
    });
    el.addEventListener("pointerleave", () => { target.style.transform = ""; });
  }
  if (stack) tilt(stack, stack.querySelector(".stack-tilt"), 10);
  document.querySelectorAll(".card").forEach((c) => tilt(c, c.querySelector(".ph"), 9));

  // 3. Filters + search over the recipe grid.
  const grid = document.querySelector("[data-grid]");
  if (!grid) return;
  const cards = [...grid.querySelectorAll(".card")];
  const chips = [...document.querySelectorAll(".chip")];
  const count = document.querySelector(".count");
  const none = document.querySelector("[data-none]");
  const q = document.querySelector("[data-search]");
  const state = { protein: 0, time: 0, veg: false, text: "" };

  const params = new URLSearchParams(location.search);
  if (q && params.get("q")) { q.value = params.get("q"); state.text = q.value.toLowerCase(); }

  function apply() {
    let shown = 0;
    for (const c of cards) {
      const d = c.dataset;
      const ok = (+d.protein >= state.protein) &&
                 (!state.time || +d.minutes <= state.time) &&
                 (!state.veg || d.veg === "1") &&
                 (!state.text || d.text.includes(state.text));
      c.hidden = !ok;
      if (ok) shown++;
    }
    if (count) count.textContent = `${shown} recipe${shown === 1 ? "" : "s"}`;
    if (none) none.hidden = shown !== 0;
  }

  chips.forEach((chip) => chip.addEventListener("click", () => {
    const group = chip.dataset.group;
    const value = chip.dataset.value;
    const wasOn = chip.getAttribute("aria-pressed") === "true";
    if (group !== "veg") chips.filter((c) => c.dataset.group === group).forEach((c) => c.setAttribute("aria-pressed", "false"));
    chip.setAttribute("aria-pressed", String(!wasOn));
    if (group === "veg") state.veg = !wasOn;
    else state[group] = wasOn ? 0 : +value;
    apply();
  }));
  if (q) q.addEventListener("input", () => { state.text = q.value.trim().toLowerCase(); apply(); });
  apply();
})();
