// The panel's whole client side: fill the field from a chip, post the answer,
// and show a refusal next to the field that caused it. No matching happens
// here — `referat label --speaker ... --name ...` does all of it.
(function () {
  const vscode = acquireVsCodeApi();

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const form = chip.closest(".name-form");
      const input = form.querySelector('input[name="name"]');
      input.value = chip.dataset.name;
      input.focus();
    });
  });

  document.querySelectorAll(".name-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = form.querySelector('input[name="name"]');
      const name = input.value.trim();
      if (!name) {
        return;
      }
      // Disabled until the host answers, so a double-click cannot file the
      // same voiceprint twice.
      form.querySelector('button[type="submit"]').disabled = true;
      form.querySelector(".error").hidden = true;
      vscode.postMessage({ type: "apply", speaker: form.dataset.speaker, name });
    });
  });

  window.addEventListener("message", (event) => {
    const message = event.data;
    if (message.type !== "refused") {
      return;
    }
    const form = document.querySelector(`.name-form[data-speaker="${message.speaker}"]`);
    if (!form) {
      return;
    }
    const error = form.querySelector(".error");
    error.textContent = message.message;
    error.hidden = false;
    form.querySelector('button[type="submit"]').disabled = false;
  });

  const first = document.querySelector('.name-form input[name="name"]');
  if (first) {
    first.focus();
  }
})();
