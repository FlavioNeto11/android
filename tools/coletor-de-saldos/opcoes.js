// Coletor de saldos (ADR-051) — página de opções: endereço da plataforma, intervalo e o resultado da última coleta.
'use strict';
var $ = function (id) { return document.getElementById(id); };

async function carregar() {
  var c = await chrome.storage.local.get(['plataforma', 'intervaloMin', 'ativo', 'ultimo']);
  $('plataforma').value = c.plataforma || 'http://127.0.0.1:8000';
  $('intervalo').value = c.intervaloMin || 60;
  $('ativo').checked = c.ativo !== false;
  var corpo = $('ultimo');
  corpo.textContent = '';
  var ultimo = c.ultimo || {};
  Object.keys(ultimo).sort().forEach(function (conta) {
    var u = ultimo[conta];
    var tr = document.createElement('tr');
    var valor = u.ok ? (u.balance !== undefined ? u.currency + ' ' + u.balance.toFixed(2) + ' (' + u.origem + ')' : 'ok')
      : 'falhou: ' + u.erro;
    [conta, valor, new Date(u.quando).toLocaleString('pt-BR')].forEach(function (txt, i) {
      var td = document.createElement('td');
      td.textContent = txt;
      if (i === 1) td.className = u.ok ? 'ok' : 'erro';
      tr.appendChild(td);
    });
    corpo.appendChild(tr);
  });
}

$('salvar').addEventListener('click', async function () {
  await chrome.storage.local.set({
    plataforma: $('plataforma').value.trim() || 'http://127.0.0.1:8000',
    intervaloMin: Math.max(15, Number($('intervalo').value) || 60),
    ativo: $('ativo').checked,
  });
  chrome.runtime.sendMessage({ tipo: 'reagendar' });
  carregar();
});
$('agora').addEventListener('click', function () { chrome.runtime.sendMessage({ tipo: 'coletar-agora' }); });
chrome.storage.onChanged.addListener(carregar);
carregar();
