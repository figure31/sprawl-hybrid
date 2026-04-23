/* =============================================================
   Sprawl — shared boot helpers for non-homepage pages.

   Pairs with sprawl-boot.css.

   Usage:
     1. <body class="boot">
     2. <link rel="stylesheet" href="sprawl-boot.css">
     3. <script src="sprawl-boot.js"></script>   (load once, early)
     4. After rendering a list:
          sprawlBoot.stagger(document.getElementById("list"));
        The helper walks direct children, tags each with .boot-item,
        and sets --boot-index inline so the CSS delays fan out.

   The boot class is removed automatically ~3.5s after the DOM is
   ready. Anything rendered or re-rendered after that point simply
   inherits default (fully-visible) styles because the boot rules
   stop matching — no animation plays.
   ============================================================= */
(function () {
  window.sprawlBoot = {
    stagger(container) {
      if (!container) return;
      let i = 0;
      for (const child of container.children) {
        child.style.setProperty("--boot-index", i++);
        child.classList.add("boot-item");
      }
    },
  };
  window.addEventListener("DOMContentLoaded", () => {
    setTimeout(() => document.body.classList.remove("boot"), 3500);
  });
})();
