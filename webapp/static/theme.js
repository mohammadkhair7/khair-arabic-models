/* Applies the colour theme before the first paint.
 *
 * Loaded render-blocking from <head>, which is the whole point: setting the
 * class from app.js instead would let the browser paint the light theme first
 * and then repaint dark, which reads as a flash on every navigation.
 *
 * The storage contract is hadith.chat's, so a reader who uses both sites gets
 * one decision honoured in both places:
 *   - `dark` class on <html>
 *   - localStorage["theme"] is "dark" or "light"
 *   - with nothing stored, dark
 *
 * Dark is the default rather than the operating system's setting: this site
 * is read, not worked in, and long vowelled Arabic sits better on a dark
 * ground. The reader's own choice still wins, and once made it is what the
 * toggle stores — the OS is never consulted, so a visitor who picks light
 * keeps light on a machine set to dark.
 *
 * app.js owns the toggle button; this file only decides the initial state.
 */
(function () {
  var stored = null;
  try {
    stored = localStorage.getItem("theme");
  } catch (e) {
    /* Storage can throw in private mode or with cookies blocked. A theme is
       not worth failing the page load over — fall through to the default. */
  }
  document.documentElement.classList.toggle("dark", stored !== "light");
})();
