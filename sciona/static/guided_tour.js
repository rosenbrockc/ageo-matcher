(function (global) {
  "use strict";

  global.initGuidedTour = function initGuidedTour(options) {
    var layer = document.getElementById("guided-tour");
    var spotlight = document.getElementById("guided-tour-spotlight");
    var dialog = document.getElementById("guided-tour-dialog");
    var progress = document.getElementById("guided-tour-progress");
    var title = document.getElementById("guided-tour-title");
    var body = document.getElementById("guided-tour-body");
    var previous = document.getElementById("guided-tour-previous");
    var next = document.getElementById("guided-tour-next");
    var close = document.getElementById("guided-tour-close");
    var steps = options.steps || [];
    var activeIndex = -1;
    var activeTarget = null;

    function clamp(value, low, high) {
      return Math.max(low, Math.min(value, high));
    }

    function positionCurrent() {
      if (!activeTarget || activeIndex < 0) return;
      var rect = activeTarget.getBoundingClientRect();
      var padding = 7;
      spotlight.style.left = Math.max(4, rect.left - padding) + "px";
      spotlight.style.top = Math.max(4, rect.top - padding) + "px";
      spotlight.style.width = Math.max(24, Math.min(window.innerWidth - 8, rect.width + padding * 2)) + "px";
      spotlight.style.height = Math.max(24, Math.min(window.innerHeight - 8, rect.height + padding * 2)) + "px";

      var dialogWidth = Math.min(360, window.innerWidth - 24);
      var dialogHeight = dialog.offsetHeight || 210;
      var left;
      var top;
      if (rect.right + dialogWidth + 15 <= window.innerWidth - 12) {
        left = rect.right + 15;
        top = clamp(rect.top, 12, window.innerHeight - dialogHeight - 12);
      } else if (rect.left - dialogWidth - 15 >= 12) {
        left = rect.left - dialogWidth - 15;
        top = clamp(rect.top, 12, window.innerHeight - dialogHeight - 12);
      } else {
        left = clamp(rect.left, 12, window.innerWidth - dialogWidth - 12);
        var below = rect.bottom + 15;
        top = below + dialogHeight <= window.innerHeight - 12
          ? below
          : Math.max(12, rect.top - dialogHeight - 15);
      }
      dialog.style.width = dialogWidth + "px";
      dialog.style.left = left + "px";
      dialog.style.top = top + "px";
    }

    function resolveTarget(step) {
      return document.querySelector(step.target) || document.body;
    }

    function renderStep(index) {
      if (index < 0 || index >= steps.length) return;
      activeIndex = index;
      var step = steps[index];
      if (options.prepareStep) options.prepareStep(step, index);
      activeTarget = resolveTarget(step);
      progress.textContent = "Step " + (index + 1) + " of " + steps.length;
      title.textContent = step.title;
      body.textContent = step.description;
      previous.disabled = index === 0;
      next.textContent = index === steps.length - 1 ? "Finish" : "Next";
      layer.classList.remove("hidden");
      requestAnimationFrame(positionCurrent);
    }

    function finish() {
      if (activeIndex >= 0 && options.onFinish) options.onFinish(steps[activeIndex], activeIndex);
      activeIndex = -1;
      activeTarget = null;
      layer.classList.add("hidden");
    }

    function start(startIndex) {
      if (!steps.length) return;
      renderStep(typeof startIndex === "number" ? startIndex : 0);
    }

    previous.addEventListener("click", function () { renderStep(activeIndex - 1); });
    next.addEventListener("click", function () {
      if (activeIndex === steps.length - 1) finish();
      else renderStep(activeIndex + 1);
    });
    close.addEventListener("click", finish);
    window.addEventListener("resize", positionCurrent);
    window.addEventListener("scroll", positionCurrent, true);
    document.addEventListener("keydown", function (event) {
      if (activeIndex < 0) return;
      if (event.key === "Escape") finish();
      if (event.target && /^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName)) return;
      if (event.key === "ArrowRight") next.click();
      if (event.key === "ArrowLeft" && activeIndex > 0) previous.click();
    });

    return {
      start: start,
      finish: finish,
      next: function () {
        if (activeIndex < 0) return;
        if (activeIndex === steps.length - 1) finish();
        else renderStep(activeIndex + 1);
      },
      getActiveIndex: function () { return activeIndex; }
    };
  };
})(window);
