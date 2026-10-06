import { Copy, Plus, Users } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from '../../api/client';
import type { AppConfig, Instance, InstagramProfile, ProfileAccount } from '../../api/types';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Checkbox, Field, Select, TextArea, TextInput } from '../../components/Field';
import { Page } from '../../components/Page';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { plural } from '../../lib/format';
import { uuid } from '../../lib/ids';
import { type LoadError, LoadErrorBanner, LoadErrorState, toLoadError } from '../../lib/loadError';
import { hashDe } from '../../lib/rotas';
import { toast } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { apiOperacoes } from './api';
import {
  ASSUNTO_MAX, ASSUNTO_MIN, MAX_ALVOS, MAX_FONTES, MAX_TETO_USD, PARAMETRO_MAX, contasDoApp, erroDoFormulario, lerRascunho, lerTeto, limparRascunho, montarCorpo,
  previaDaCapacidade, resolverAlvo, type AcaoFinal, type Rascunho, type EscolhaDoAlvo, type FormularioDaOperacao,
} from './criar';
import styles from './Operacao.module.css';

/**
 * 31.176: criar a operação pela tela (prova de 07/10). Objetivo, app, quem faz (personas que JÁ existem, com a conta e o aparelho
 * que já têm), teto de custo e ação final; antes de enviar, a previa do que vai rodar e do que nasce parado e, depois, uma
 * confirmação com o teto em destaque. Nunca cria conta nem persona. `POST /api/operacoes`, adendo v1.94/v1.95.
 */

interface Listas { apps: AppConfig[]; perfis: InstagramProfile[]; aparelhos: Instance[] }
type ContasDaPersona = ProfileAccount[] | 'erro';

const rotuloDaPersona = (p: InstagramProfile): string => p.persona_name?.trim() || p.display_name?.trim() || 'Persona sem nome';
const usd = (n: number): string => `US$ ${n.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;

const ROTULO_DA_ACAO: Record<AcaoFinal, { titulo: string; explica: string }> = {
  preparar: { titulo: 'Só preparar', explica: 'Cada agente deixa o texto gerado e a interface pronta, sem enviar. É o padrão.' },
  executar: { titulo: 'Preparar e poder executar', explica: 'Vai além do rascunho, mas só pela liberação: nenhuma ação final sai sem o texto lido por uma pessoa.' },
};

export function CriarOperacao() {
  const [listas, setListas] = useState<Listas | null>(null);
  // Lido UMA vez ao abrir (o formulário o limpa depois de montar): sem isso, o re-render já o veria vazio.
  const [rascunho] = useState(lerRascunho);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [vez, setVez] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    setErro(null);
    Promise.all([api.listApps(), api.listProfiles(), api.listInstances()])
      .then(([apps, perfis, aparelhos]) => { if (!ctl.signal.aborted) setListas({ apps, perfis, aparelhos }); })
      .catch((e: unknown) => { if (!ctl.signal.aborted) setErro(toLoadError(e)); });
    return () => ctl.abort();
  }, [vez]);

  const voltar = <a className={styles.link} href={hashDe('operacoes')}>← Todas as operações</a>;
  if (erro && !listas) return <Page title="Nova operação"><p className={styles.cabecalho}>{voltar}</p><LoadErrorState what="as personas, os apps e os aparelhos" error={erro} onRetry={() => setVez((n) => n + 1)} /></Page>;
  if (!listas) return <Page title="Nova operação"><LoadingRegion label="Lendo personas, apps e aparelhos"><Skeleton height={160} /></LoadingRegion></Page>;
  // O rascunho de "Repetir como nova" vale para esta abertura: lido aqui, limpo depois que o formulário montou.
  return <Formulario listas={listas} voltar={voltar} rascunho={rascunho} />;
}

function Formulario({ listas, voltar, rascunho }: { listas: Listas; voltar: ReactNode; rascunho: Rascunho | null }) {
  const { apps, perfis, aparelhos } = listas;
  const [command, setCommand] = useState(rascunho?.command ?? '');
  const [appId, setAppId] = useState(() => (rascunho && apps.some((a) => a.id === rascunho.appId) ? rascunho.appId : apps.find((a) => a.id === 'instagram')?.id ?? ''));
  const [acaoFinal, setAcaoFinal] = useState<AcaoFinal>(rascunho?.acaoFinal ?? 'preparar');
  const [assunto, setAssunto] = useState(rascunho?.assunto ?? '');
  const [maxUsd, setMaxUsd] = useState(rascunho?.maxUsd ?? '');
  const [fontes, setFontes] = useState(rascunho?.fontes ?? '');
  const [username, setUsername] = useState(rascunho?.username ?? '');
  const [legenda, setLegenda] = useState(rascunho?.legenda ?? '');
  const [busca, setBusca] = useState('');
  // Só as personas que ainda existem voltam marcadas; conta e aparelho começam no padrão (o que existe agora).
  const daOperacao = useMemo(() => (rascunho?.profileIds ?? []).filter((id) => perfis.some((p) => p.id === id)), [rascunho, perfis]);
  const [escolhas, setEscolhas] = useState<Record<string, EscolhaDoAlvo>>(() => Object.fromEntries(daOperacao.map((id) => [id, { conta: '', aparelho: '' }])));
  const [contas, setContas] = useState<Record<string, ContasDaPersona>>({});
  const [enviando, setEnviando] = useState(false);
  const [recusa, setRecusa] = useState<LoadError | null>(null);
  const chave = useRef<{ corpo: string; key: string } | null>(null);

  const idsDeAparelho = useMemo(() => new Set(aparelhos.map((a) => a.id)), [aparelhos]);
  const selecionados = Object.keys(escolhas);
  const form: FormularioDaOperacao = { command, appId, acaoFinal, assunto, maxUsd, selecionados, fontes, username, legenda };
  const carregarContas = (id: string) => {
    api.listAccounts(id).then((c) => setContas((x) => ({ ...x, [id]: c }))).catch(() => setContas((x) => ({ ...x, [id]: 'erro' })));
  };
  useEffect(() => {
    daOperacao.forEach(carregarContas);
    limparRascunho();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const alternar = (p: InstagramProfile, ligado: boolean) => {
    setEscolhas((e) => {
      const n = { ...e };
      if (ligado) n[p.id] = { conta: '', aparelho: '' };
      else delete n[p.id];
      return n;
    });
    if (ligado && !contas[p.id]) carregarContas(p.id);
  };
  const mudar = (id: string, parcial: Partial<EscolhaDoAlvo>) => setEscolhas((e) => (e[id] ? { ...e, [id]: { ...e[id]!, ...parcial } } : e));
  // Trocar de app muda quais contas servem: a escolha de conta de um app não vale no outro.
  const trocarApp = (id: string) => {
    setAppId(id);
    setEscolhas((e) => Object.fromEntries(Object.entries(e).map(([k, v]) => [k, { ...v, conta: '' }])));
  };

  const lidas = selecionados.every((id) => Array.isArray(contas[id]));
  const resolvidos = useMemo(
    () => selecionados.map((id) => resolverAlvo(id, Array.isArray(contas[id]) ? contas[id] : [], appId, escolhas[id]!, idsDeAparelho)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [escolhas, contas, appId, idsDeAparelho],
  );
  const previa = previaDaCapacidade(resolvidos);
  const invalido = erroDoFormulario(form) ?? (lidas ? null : 'Espere ler as contas das personas escolhidas.');
  const visiveis = perfis.filter((p) => !busca.trim() || rotuloDaPersona(p).toLowerCase().includes(busca.trim().toLowerCase()));
  const nomeDe = (id: string) => rotuloDaPersona(perfis.find((p) => p.id === id)!);

  async function enviar() {
    if (invalido || enviando) return;
    const corpo = montarCorpo(form, resolvidos);
    const teto = lerTeto(maxUsd) ?? 0;
    const r = await confirm({
      title: 'Criar a operação?', confirmLabel: 'Criar a operação', cancelLabel: 'Voltar',
      body: (
        <div data-confirma-operacao>
          <p><strong data-teto-em-destaque>Teto de custo: {usd(teto)}</strong>, somado em todos os agentes. Atingido o teto, a IA deixa de ser chamada.</p>
          <p>{plural(previa.solicitados, 'persona', 'personas')}: {previa.aptos} {previa.aptos === 1 ? 'vai rodar' : 'vão rodar'}
            {previa.paradosNaCriacao > 0 ? `, ${plural(previa.paradosNaCriacao, 'nasce parado', 'nascem parados')} (sem conta, sessão ou aparelho)` : ''}.</p>
          <p>{ROTULO_DA_ACAO[acaoFinal].titulo}: {ROTULO_DA_ACAO[acaoFinal].explica}</p>
        </div>
      ),
    });
    if (!r.confirmed) return;
    // A nova tentativa do MESMO corpo reaproveita a chave (o servidor devolve a mesma operação); outro corpo, outra chave.
    const texto = JSON.stringify(corpo);
    if (chave.current?.corpo !== texto) chave.current = { corpo: texto, key: uuid() };
    setEnviando(true);
    setRecusa(null);
    try {
      const op = await apiOperacoes.criar(corpo, chave.current.key);
      toast({ tone: 'success', title: 'Operação criada', message: `${plural(previa.aptos, 'agente começa', 'agentes começam')} agora.` });
      useUiStore.getState().navegar({ tela: 'operacoes', segmentos: [op.id] });
    } catch (e) {
      setRecusa(toLoadError(e));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <Page title="Nova operação" lead="Um objetivo entregue a várias personas, cada uma com a conta e o aparelho que ela já tem. Nada aqui cria conta nem persona.">
      <p className={styles.cabecalho}>{voltar}</p>
      {recusa ? <LoadErrorBanner error={recusa} /> : null}
      <form className={styles.formulario} onSubmit={(e) => { e.preventDefault(); void enviar(); }}>
        <Field label="Objetivo" hint="Em português, como no Comando. Nunca coloque senha nem código aqui.">
          {({ id }) => <TextArea id={id} rows={3} value={command} placeholder="Ex.: comentar na última publicação do perfil alvo" onChange={(e) => setCommand(e.target.value)} />}
        </Field>
        <div className={styles.filtros}>
          <Field label="App">
            {({ id }) => (
              <Select id={id} value={appId} onChange={(e) => trocarApp(e.target.value)}>
                <option value="">Escolha o app</option>
                {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Teto de custo (US$)" hint={`Obrigatório, até ${MAX_TETO_USD}. Vale para a operação inteira.`}>
            {({ id }) => <TextInput id={id} inputMode="decimal" value={maxUsd} placeholder="Ex.: 2,50" onChange={(e) => setMaxUsd(e.target.value)} />}
          </Field>
          <Field label="Ação final">
            {({ id }) => (
              <Select id={id} value={acaoFinal} onChange={(e) => setAcaoFinal(e.target.value === 'executar' ? 'executar' : 'preparar')}>
                {(Object.keys(ROTULO_DA_ACAO) as AcaoFinal[]).map((k) => <option key={k} value={k}>{ROTULO_DA_ACAO[k].titulo}</option>)}
              </Select>
            )}
          </Field>
        </div>
        <p className={styles.mudo}>{ROTULO_DA_ACAO[acaoFinal].explica}</p>
        {rascunho ? (
          <Banner tone="info" icon={Copy} compact role="status" title="Copiado de uma operação anterior">
            Objetivo, app, teto, fontes e personas vieram da operação anterior; conta e aparelho são resolvidos de novo, com o que existe agora.
            {rascunho.profileIds.length > daOperacao.length ? ` ${plural(rascunho.profileIds.length - daOperacao.length, 'persona não existe mais e ficou de fora', 'personas não existem mais e ficaram de fora')}.` : ''}
            {rascunho.parametrosNaoCopiados.length ? ` Parâmetros fixos que este formulário não oferece não foram copiados: ${rascunho.parametrosNaoCopiados.join(', ')}.` : ''}
          </Banner>
        ) : null}
        <Field label="Assunto (opcional)" hint={`O que precisa ser compreendido antes (${ASSUNTO_MIN} a ${ASSUNTO_MAX} caracteres).`}>
          {({ id }) => <TextInput id={id} value={assunto} onChange={(e) => setAssunto(e.target.value)} />}
        </Field>
        <Field label="Fontes públicas (opcional)" hint={`Uma URL https:// por linha, até ${MAX_FONTES}, sem usuário nem parâmetros (?…). É a única origem da pesquisa externa.`}>
          {({ id }) => <TextArea id={id} rows={2} mono value={fontes} placeholder="https://exemplo.com.br/lancamento" onChange={(e) => setFontes(e.target.value)} />}
        </Field>
        <div className={styles.filtros}>
          <Field label="Perfil alvo (opcional)" hint="Fixa o alvo quando o comando não diz.">
            {({ id }) => <TextInput id={id} maxLength={PARAMETRO_MAX} value={username} onChange={(e) => setUsername(e.target.value)} />}
          </Field>
          <Field label="Trecho da legenda (opcional)" hint="Fixa a publicação quando o comando não diz.">
            {({ id }) => <TextInput id={id} maxLength={PARAMETRO_MAX} value={legenda} onChange={(e) => setLegenda(e.target.value)} />}
          </Field>
        </div>

        <section aria-labelledby="nova-operacao-personas">
          <h2 id="nova-operacao-personas" className={styles.subtitulo}>Personas ({plural(selecionados.length, 'escolhida', 'escolhidas')} de {perfis.length}; até {MAX_ALVOS})</h2>
          {perfis.length === 0 ? (
            <Banner tone="warning" icon={Users} compact title="Nenhuma persona cadastrada" role="status">Esta tela só escolhe entre as personas que já existem; ela não cria nenhuma.</Banner>
          ) : (
            <>
              <Field label="Buscar persona">{({ id }) => <TextInput id={id} small value={busca} onChange={(e) => setBusca(e.target.value)} />}</Field>
              <ul className={styles.lista} aria-label="Personas">
                {visiveis.map((p) => {
                  const marcada = !!escolhas[p.id];
                  const r = marcada ? resolvidos.find((x) => x.profileId === p.id) : undefined;
                  const lista = contas[p.id];
                  const candidatas = Array.isArray(lista) ? contasDoApp(lista, appId) : [];
                  return (
                    <li key={p.id} className={styles.itemDaLista} data-persona={p.id}>
                      <Checkbox label={rotuloDaPersona(p)} checked={marcada} onChange={(e) => alternar(p, e.target.checked)} />
                      {marcada ? (
                        <div className={styles.filtros}>
                          {lista === undefined ? <span className={styles.mudo}>Lendo as contas…</span> : null}
                          {lista === 'erro' ? <span className={styles.mudo} role="alert">Não foi possível ler as contas desta persona.</span> : null}
                          {Array.isArray(lista) ? (
                            <>
                              <Field label="Conta">
                                {({ id }) => (
                                  <Select id={id} small value={escolhas[p.id]!.conta} disabled={candidatas.length === 0} onChange={(e) => mudar(p.id, { conta: e.target.value })}>
                                    <option value="">{candidatas.length ? 'Conta ativa (padrão)' : 'Sem conta neste app'}</option>
                                    {candidatas.map((c) => <option key={c.id} value={c.id}>{c.handle}</option>)}
                                  </Select>
                                )}
                              </Field>
                              <Field label="Aparelho">
                                {({ id }) => (
                                  <Select id={id} small value={escolhas[p.id]!.aparelho} onChange={(e) => mudar(p.id, { aparelho: e.target.value })}>
                                    <option value="">Onde a sessão está (padrão)</option>
                                    {aparelhos.map((a) => <option key={a.id} value={a.id}>{a.id}</option>)}
                                  </Select>
                                )}
                              </Field>
                            </>
                          ) : null}
                        </div>
                      ) : null}
                      {r ? (
                        <span className={styles.mudo} data-situacao={r.situacao}>
                          {r.motivo}{r.instanceId ? ` Aparelho: ${r.instanceId}.` : ''}{r.aviso ? ` ${r.aviso}` : ''}
                        </span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </section>

        <section aria-labelledby="nova-operacao-previa">
          <h2 id="nova-operacao-previa" className={styles.subtitulo}>O que vai acontecer</h2>
          <dl className={styles.celulas} data-previa>
            {[
              ['Escolhidas', previa.solicitados], ['Com conta', previa.comConta], ['Com sessão pronta', previa.comSessao],
              ['Com aparelho (vão rodar)', previa.aptos], ['Nascem paradas', previa.paradosNaCriacao],
            ].map(([rotulo, valor]) => (
              <div key={rotulo} className={styles.celula}>
                <dd className={styles.celulaValor}>{valor}</dd>
                <dt className={styles.celulaRotulo}>{rotulo}</dt>
              </div>
            ))}
          </dl>
          {previa.paradosNaCriacao > 0 ? (
            <p className={styles.mudo} role="status">
              {nomeDe(resolvidos.find((r) => r.situacao !== 'apto')!.profileId)}
              {previa.paradosNaCriacao > 1 ? ` e mais ${previa.paradosNaCriacao - 1}` : ''} {previa.paradosNaCriacao > 1 ? 'não têm' : 'não tem'} tudo o que a operação precisa: nascem paradas, com o motivo, e a operação não troca de app.
            </p>
          ) : null}
        </section>

        <div className={styles.acoesDoRelatorio}>
          <Button type="submit" variant="primary" icon={Plus} loading={enviando} disabledReason={invalido}>Criar a operação</Button>
          <a className={styles.link} href={hashDe('operacoes')}>Cancelar</a>
        </div>
      </form>
    </Page>
  );
}
