/* Applies the colour theme before the first paint.
 *
 * Loaded render-blocking from <head>, which is the whole point: setting the
 * class from app.js instead would let the browser paint the light theme first
 * and then repaint dark, which reads as a flash on every navigation.
 *
 * The contract is deliberately identical to hadith.chat's, so a reader who
 * uses both sites gets one decision honoured in both places:
 *   - `dark` class on <html>
 *   - localStorage["theme"] is "dark" or "light"
 *   - with nothing stored, follow the operating system
 *
 * app.js owns the toggle button; this file only decides the initial state.
 */
(function () {
  var stored = null;
  try {
    stored = localStorage.getItem("theme");
  } catch (e) {
    /* Storage can throw in private mode or with cookies blocked. A theme is
       not worth failing the page load over — fall through to the OS setting. */
  }
  var dark = stored
    ? stored === "dark"
    : window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.classList.toggle("dark", dark);
})();
