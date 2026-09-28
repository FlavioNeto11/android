// Coletor de saldos (ADR-051) — service worker da extensão.
//
// De hora em hora (configurável) pergunta à plataforma quais consoles abrir (`GET /api/ai/balances`, campo
// `console` de cada conta), abre os três numa janela minimizada, recebe o saldo que `pagina.js` achou em cada um,
// registra na plataforma (`POST /api/ai/balances/{conta}`, `source: "coletor"`) e fecha a janela. Também registra
// quando o próprio dono abre um console (no máximo uma vez a cada 10 min por conta).
//
// Nenhuma credencial sai do navegador: as páginas abrem na sessão que o Chrome já tem, e à plataforma só chega o
// número. Deslogado, o console não mostra saldo, e então nada é enviado — a plataforma avisa pela leitura velha.
importScripts('leitura.js');

var PADRAO = { plataforma: 'http://127.0.0.1:8000', intervaloMin: 60, ativo: true };
var PRAZO_DA_COLETA_MIN = 1.5;
var INTERVALO_PASSIVO_MS = 10 * 60 * 1000;

async function config() {
  var c = await chrome.storage.local.get(['plataforma', 'intervaloMin', 'ativo']);
  return {
    plataforma: String(c.plataforma || PADRAO.plataforma).replace(/\/+$/, ''),
    intervaloMin: Math.max(15, Number(c.intervaloMin) || PADRAO.intervaloMin),
    ativo: c.ativo !== false,
  };
}

async function anotar(conta, dados) {
  var s = await chrome.storage.local.get(['ultimo']);
  var ultimo = s.ultimo || {};
  ultimo[conta] = Object.assign({ quando: new Date().toISOString() }, dados);
  await chrome.storage.local.set({ ultimo: ultimo });
}

async function agendar() {
  var c = await config();
  await chrome.alarms.clear('coletar');
  if (c.ativo) chrome.alarms.create('coletar', { delayInMinutes: 1, periodInMinutes: c.intervaloMin });
}

async function enviar(conta, balance, currency, origem) {
  var c = await config();
  try {
    var r = await fetch(c.plataforma + '/api/ai/balances/' + conta, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ balance: balance, currency: currency, source: 'coletor',
        observed_at: new Date().toISOString(), note: 'coletor do Chrome (' + origem + ')' }),
    });
    if (!r.ok) throw new Error('a plataforma respondeu ' + r.status);
    await anotar(conta, { ok: true, balance: balance, currency: currency, origem: origem });
  } catch (e) {
    await anotar(conta, { ok: false, erro: String(e && e.message || e), origem: origem });
  }
}

async function coletar() {
  var sessao = (await chrome.storage.session.get(['sessao'])).sessao;
  if (sessao && Date.now() - sessao.inicio < 3 * 60 * 1000) return;          // já há uma coleta aberta
  var c = await config();
  var contas;
  try {
    var r = await fetch(c.plataforma + '/api/ai/balances');
    if (!r.ok) throw new Error('a plataforma respondeu ' + r.status);
    contas = ((await r.json()).accounts || []).filter(function (a) {
      return typeof a.console === 'string' && a.console.indexOf('https://') === 0;
    });
  } catch (e) {
    await anotar('plataforma', { ok: false, erro: 'sem acesso a ' + c.plataforma + ': ' + String(e && e.message || e) });
    return;
  }
  await anotar('plataforma', { ok: true });
  if (!contas.length) return;
  // Minimizada, para não roubar a tela. Se uma coleta minimizada já deixou conta sem leitura (página que só
  // desenha visível), a próxima usa uma janela normal, sem foco.
  var normal = (await chrome.storage.local.get(['janelaNormal'])).janelaNormal === true;
  var janela = await chrome.windows.create(Object.assign(
    { url: contas.map(function (a) { return a.console; }), focused: false },
    normal ? { state: 'normal', width: 900, height: 700 } : { state: 'minimized' }));
  await chrome.storage.session.set({ sessao: {
    windowId: janela.id, inicio: Date.now(), normal: normal,
    esperando: contas.map(function (a) { return a.account; }) } });
  chrome.alarms.create('encerrar', { delayInMinutes: PRAZO_DA_COLETA_MIN });
}

async function encerrar(porTempo) {
  var sessao = (await chrome.storage.session.get(['sessao'])).sessao;
  if (!sessao) return;
  await chrome.storage.session.remove('sessao');
  await chrome.alarms.clear('encerrar');
  if (porTempo && sessao.esperando.length) {
    for (var i = 0; i < sessao.esperando.length; i++) {
      await anotar(sessao.esperando[i], { ok: false, erro: 'o saldo não apareceu na página (deslogado ou tela mudou)' });
    }
    if (!sessao.normal) await chrome.storage.local.set({ janelaNormal: true });
  }
  try { await chrome.windows.remove(sessao.windowId); } catch (e) { /* o dono já fechou */ }
}

chrome.runtime.onMessage.addListener(function (msg, sender) {
  if (!msg || msg.tipo !== 'saldo') {
    if (msg && msg.tipo === 'coletar-agora') coletar();
    if (msg && msg.tipo === 'reagendar') agendar();
    return;
  }
  var tab = sender && sender.tab;
  if (!tab || !coletorLeitura.topoValido(msg.conta, tab.url || '')) return;
  if (typeof msg.balance !== 'number' || !isFinite(msg.balance)) return;
  (async function () {
    var sessao = (await chrome.storage.session.get(['sessao'])).sessao;
    if (sessao && tab.windowId === sessao.windowId) {
      await enviar(msg.conta, msg.balance, msg.currency, 'coleta');
      sessao.esperando = sessao.esperando.filter(function (c) { return c !== msg.conta; });
      if (!sessao.esperando.length) await encerrar(false);
      else await chrome.storage.session.set({ sessao: sessao });
      return;
    }
    // O dono abriu o console por conta própria: vale como leitura também, sem inundar a plataforma.
    var chave = 'passivo_' + msg.conta;
    var ultimo = (await chrome.storage.session.get([chave]))[chave] || 0;
    if (Date.now() - ultimo < INTERVALO_PASSIVO_MS) return;
    await chrome.storage.session.set({ [chave]: Date.now() });
    await enviar(msg.conta, msg.balance, msg.currency, 'página aberta');
  })();
});

chrome.alarms.onAlarm.addListener(function (alarme) {
  if (alarme.name === 'coletar') coletar();
  if (alarme.name === 'encerrar') encerrar(true);
});
chrome.runtime.onInstalled.addListener(agendar);
chrome.runtime.onStartup.addListener(agendar);
