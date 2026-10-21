(function () {
  var f = document.getElementById('f'), body = f.body, sel = f.resume, warn = document.getElementById('warn');
  var web = document.getElementById('web'), account = f.dataset.account, max = parseInt(f.dataset.max, 10);
  function enc(s) { return encodeURIComponent(s).replace(/[!'()*]/g, function (c) { return '%' + c.charCodeAt(0).toString(16).toUpperCase(); }); }
  function setResume(text, url) {
    var lines = text.split('\n'), i = -1;
    for (var k = 0; k < lines.length; k++) { if (lines[k].indexOf('Resume:') === 0) { i = k; break; } }
    if (url) {
      if (i < 0) return text + '\n\nResume: ' + url;
      lines[i] = 'Resume: ' + url; return lines.join('\n');
    }
    if (i < 0) return text;
    lines.splice(i, 1);
    if (i > 0 && i < lines.length && lines[i] === '' && lines[i - 1] === '') lines.splice(i, 1);
    return lines.join('\n').replace(/\n+$/, i >= lines.length ? '' : '$&');
  }
  function url() {
    var u = sel.options[sel.selectedIndex].dataset.url || '';
    return 'https://mail.google.com/mail/?authuser=' + enc(account) + '&view=cm&fs=1&to=' + enc(f.to_email.value) +
      '&su=' + enc(f.subject.value) + '&body=' + enc(setResume(body.value.replace(/\r\n/g, '\n'), u));
  }
  function check() { var u = url(); web.href = u; warn.hidden = u.length <= max; }
  sel.addEventListener('change', function () {
    body.value = setResume(body.value.replace(/\r\n/g, '\n'), sel.options[sel.selectedIndex].dataset.url || '');
    check();
  });
  f.addEventListener('input', check);
  document.getElementById('copy').addEventListener('click', function () {
    var b = this, t = body.value;
    function done() { b.textContent = 'Copied'; setTimeout(function () { b.textContent = 'Copy body'; }, 1500); }
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(t).then(done); }
    else { body.select(); document.execCommand('copy'); done(); }
  });
  check();
})();
