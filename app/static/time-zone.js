// Saves the browser's own time zone for a signed-in person who has none yet.
// Runs only when the page says the zone is missing, tries once per browser session, and never loops:
// the flag is set before the form is sent, and a refused value is not tried again.
(function () {
  "use strict";
  var form = document.getElementById("auto-time-zone");
  if (!form || document.body.getAttribute("data-time-zone") !== "missing") return;
  var zone;
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return; }
  if (typeof zone !== "string" || zone === "") return;
  try {
    if (window.sessionStorage.getItem("spotter-time-zone-tried")) return;
    window.sessionStorage.setItem("spotter-time-zone-tried", "1");
  } catch (e) { return; } // no session storage: do nothing rather than risk a loop
  form.querySelector('input[name="time_zone"]').value = zone;
  form.submit();
})();
