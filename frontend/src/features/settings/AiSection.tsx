import { Bot, CircleDollarSign, FlaskConical, KeyRound, RefreshCw, ServerCrash, ShieldAlert, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { AiStatus } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { KvList, KvRow } from '../../components/JsonTree';
import { PageSection, TableWrap } from '../../components/Page';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { balanceBrief, balanceOfRole, balanceStateLabel, balanceTone, balancesByUrgency } from '../../lib/aiBalance';
import {
  aiFeatureRows, aiModelRows, aiProfileRows, aiRoleRows, decisaoFechadaConsumidoresLabel, effortLabel, esquemaDoPlanoLabel,
  leituraVisualLabel, spendLabel,
} from '../../lib/aiLabels';
import { useAppStore } from '../../store/app';
import { EXTERNAL_DATA_NOTICE } from '../topbar/TopBar';
import { AiBalances } from './AiBalances';
import { ContextRetrievalSection } from './ContextRetrievalSection';
import styles from './Settings.module.css';

export function AiSection() {
  const fromHealth = useAppStore((s) => s.health?.ai ?? null);
  const features = useAppStore((s) => s.health?.features ?? null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setAi(await api.ai());
    } catch (e) {
      const err = toApiError(e);
      setError({ message: err.message, hint: hintForError(err) });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Se GET /api/ai falhar, o status que veio no health continua útil (com o erro visível).
  const status = ai ?? fromHealth;

  if (loading && !status) {
    return (
      <LoadingRegion label="Consultando o status da IA…" className={styles.stack}>
        <Skeleton width={280} height={18} />
        <Skeleton height={90} radius={8} />
      </LoadingRegion>
    );
  }
  if (!status) {
    return (
      <EmptyState icon={ServerCrash} tone="danger" title="Não foi possível consultar a IA" hint={error?.hint} actions={<Button variant="outline" icon={RefreshCw} onClick={() => void load()}>Tentar de novo</Button>}>
        {error?.message}
      </EmptyState>
    );
  }

  const usable = (status.configured || status.simulated) && !status.account_blocked;
  // Hub de IA (item 7.1): quando o backend informa as funções, ELAS são a verdade sobre "para onde isto vai".
  const papeis = aiRoleRows(status);
  const perfis = aiProfileRows(status);
  const leituraVisual = leituraVisualLabel(status);
  const decisaoFechada = decisaoFechadaConsumidoresLabel(status);
  const gastoHoje = spendLabel(status.spend_today_usd, status.spend_limit_day_usd);
  // O fallback de recusa é por função quando há funções (vai no cartão delas); sem o hub, fica na situação geral.
  const fallbackDeRecusa = status.refusal_fallback ? (
    <Banner tone="info" icon={CircleDollarSign} title="Fallback pago de recusa está ligado" compact>
      Quando o classificador do provedor recusa uma requisição, ela é reexecutada no servidor dele em outro
      modelo — destino {status.refusal_fallback_target ?? 'definido pelo provedor'} — e a tentativa é cobrada na
      tarifa do modelo que respondeu. A troca aparece na linha do tempo da execução e no relatório de custo.
      Para desligar: <span className="mono">AI_REFUSAL_FALLBACK=false</span> no <span className="mono">.env</span>,
      ou por função em <span className="mono">ai.roles.&lt;papel&gt;.refusal_fallback</span>.
    </Banner>
  ) : null;

  return (
    <>
      {error ? <Banner tone="warning" icon={ServerCrash} compact title="Mostrando o último status conhecido">{error.message} {error.hint}</Banner> : null}

      {status.account_blocked ? (
        <Banner tone="danger" icon={ShieldAlert} title="Disjuntor de conta de IA acionado" role="alert">
          {status.account_blocked_reason ?? 'O provedor recusou a última chamada por cobrança ou credencial.'} A execução afetada foi pausada automaticamente e nenhuma tentativa foi gasta. Corrija e retome a execução para soltar o disjuntor.
        </Banner>
      ) : status.simulated ? (
        <Banner tone="warning" icon={FlaskConical} title="MODO SIMULADO" role="status">
          Nenhuma IA real é chamada: planos e resultados são fictícios, úteis só para testar o painel. Para usar a IA de verdade, configure a chave e desative o modo simulado no <span className="mono">.env</span> do backend.
        </Banner>
      ) : !status.configured ? (
        <Banner tone="danger" icon={ShieldAlert} title="IA não configurada" role="alert">
          Planejar e Executar ficam indisponíveis até a chave ser definida. O restante do painel (emuladores, controle manual, diagnóstico) continua funcionando.
        </Banner>
      ) : null}

      {/* Situação e "onde fica a chave" lado a lado quando cabem; uma coluna quando o Foco aperta a página. */}
      <div className={styles.aiGrid}>
        <PageSection
          title="Situação"
          subtitle="Provedor de IA usado para planejar e operar os aparelhos. Esta tela é somente leitura."
          actions={<Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={() => void load()}>Atualizar</Button>}
          bodyClassName={styles.stack}
        >
          <KvList>
            <KvRow label="Situação">
              {usable ? <Badge tone={status.simulated ? 'warning' : 'success'} icon={status.simulated ? FlaskConical : ShieldCheck}>{status.simulated ? 'Simulada' : 'Pronta para uso'}</Badge> : <Badge tone="danger" icon={ShieldAlert}>{status.account_blocked ? 'Bloqueada (disjuntor)' : 'Indisponível'}</Badge>}
            </KvRow>
            <KvRow label="Provedor"><span className="mono">{status.provider}</span></KvRow>
            <KvRow label="Modelo"><span className="mono">{status.model ?? '—'}</span></KvRow>
            {aiModelRows(status).map((m) => (
              <KvRow key={m.key} label={`Modelo — ${m.label.toLowerCase()}`}><span className="mono">{m.value}</span></KvRow>
            ))}
            <KvRow label="Esforço de raciocínio">{effortLabel(status.effort)}</KvRow>
            {status.esquema_do_plano ? <KvRow label="Esquema do plano">{esquemaDoPlanoLabel(status.esquema_do_plano)}</KvRow> : null}
            {status.profiles ? (
              <KvRow label="Perfis de IA">
                {perfis.length ? perfis.map((p) => p.name).join(', ') : <span className={styles.muted}>nenhum (todas as execuções usam o padrão)</span>}
              </KvRow>
            ) : null}
            {leituraVisual ? <KvRow label="Leitura visual">{leituraVisual}</KvRow> : null}
            {decisaoFechada ? (
              <KvRow label="Decisão fechada (Jev)">
                {decisaoFechada}{' '}
                <Badge tone={status.decisao_fechada?.sending ? 'warning' : 'muted'}>
                  {status.decisao_fechada?.sending ? 'envio ativo' : 'nada sai agora'}
                </Badge>
              </KvRow>
            ) : null}
            {aiFeatureRows(status, features).map((f) => <KvRow key={f.key} label={f.label}>{f.value}</KvRow>)}
            <KvRow label="Chave de API">{status.configured ? 'Presente no backend' : 'Ausente'}</KvRow>
            <KvRow label="Dados saem da máquina?">{status.sends_data_externally ? 'Sim' : 'Não'}</KvRow>
            {gastoHoje ? <KvRow label="Gasto de hoje (UTC)">{gastoHoje}</KvRow> : null}
            {balancesByUrgency(status.balances).map((b) => (
              <KvRow key={b.account} label={`Saldo — ${b.label}`}>
                <Badge tone={balanceTone(b)}>{balanceStateLabel(b.state)}</Badge> {balanceBrief(b)}
              </KvRow>
            ))}
          </KvList>
          {status.notice ? <p className={styles.aiNotice}>{status.notice}</p> : null}
          {status.sends_data_externally ? (
            <Banner tone="warning" icon={ShieldAlert} title="Dados enviados para fora desta máquina">
              {EXTERNAL_DATA_NOTICE}. Não use contas pessoais nem dados reais nos emuladores: tudo o que aparece na tela pode ser enviado.
            </Banner>
          ) : (
            <Banner tone="success" icon={ShieldCheck} title="Nada sai desta máquina">No modo atual, screenshots e textos das telas ficam apenas no backend local.</Banner>
          )}
          {papeis.length === 0 ? fallbackDeRecusa : null}
          <p className={styles.aiFootnote}><Bot size={12} aria-hidden className={styles.inlineIcon} /> O modelo em uso também aparece na barra superior.</p>
        </PageSection>

        <PageSection title={<><KeyRound size={16} aria-hidden className={styles.inlineIcon} /> Onde fica a chave</>}>
          <p>A chave do provedor vive no arquivo <span className="mono">.env</span> do backend — <strong>nunca no navegador</strong>, e este painel não tem campo para ela.</p>
          <ol className={styles.steps}>
            <li>Abra o <span className="mono">.env</span> na pasta do backend.</li>
            <li>Defina a chave do provedor de IA (veja o <span className="mono">.env.example</span> para o nome da variável).</li>
            <li>Reinicie o backend e clique em “Atualizar”.</li>
          </ol>
        </PageSection>
      </div>

      <AiBalances />

      <ContextRetrievalSection />

      {papeis.length > 0 ? (
        <PageSection
          title="Por função"
          subtitle="Cada função pode ter provedor, endpoint e modelo próprios. “Dados saem?” é respondido por função — com um modelo local no ator e um provedor externo no planejador, uma resposta única deixaria de ser verdade."
          bodyClassName={styles.stack}
        >
          {papeis.length > 0 ? (
            // Seis colunas não cabem num celular: rolam dentro da região em vez de serem cortadas pelo main.
            <TableWrap label="Funções da IA">
              <table className={styles.aiRoles}>
                <thead>
                  <tr>
                    <th scope="col">Função</th><th scope="col">Provedor</th><th scope="col">Modelo</th>
                    <th scope="col">Endpoint</th><th scope="col">Esforço</th><th scope="col">Raciocínio</th>
                    <th scope="col">Conta · saldo</th><th scope="col">Dados saem?</th><th scope="col">Se falhar</th>
                  </tr>
                </thead>
                <tbody>
                  {papeis.map((p) => (
                    <tr key={p.key}>
                      <th scope="row">{p.label}</th>
                      <td className="mono">{p.provider}</td>
                      <td className="mono">{p.model}{p.warnings.length ? <><br /><small className={styles.muted}>{p.warnings.join(' · ')}</small></> : null}</td>
                      <td className="mono">{p.endpoint}</td>
                      <td>{p.effort}</td>
                      <td>{p.thinking}</td>
                      <td>
                        {(() => {
                          const conta = balanceOfRole(status.balances, p.key);
                          return conta
                            ? <Badge tone={balanceTone(conta)}>{balanceBrief(conta)}</Badge>
                            : <span className={styles.muted}>sem conta paga</span>;
                        })()}
                      </td>
                      <td><Badge tone={p.external ? 'warning' : 'success'}>{p.external ? 'Sim' : 'Não'}</Badge></td>
                      <td>
                        {p.fallback
                          ? <>cai para <span className="mono">{p.fallback}</span></>
                          : <span className={styles.muted}>o erro sobe (sem fallback pago)</span>}
                        {p.refusalFallback ? <><br /><small className={styles.muted}>recusa: reexecutada pelo provedor</small></> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          ) : null}

          {fallbackDeRecusa}
        </PageSection>
      ) : null}

      {perfis.length > 0 ? (
        <PageSection
          title="Perfis de IA"
          subtitle="Uma execução pode escolher um perfil (ou cair nele pelo canário). Cada perfil muda só o que está listado; o resto é o padrão da tabela “Por função”."
          bodyClassName={styles.stack}
        >
          <TableWrap label="Perfis de IA">
            <table className={styles.aiRoles}>
              <thead>
                <tr>
                  <th scope="col">Perfil</th><th scope="col">O que muda</th><th scope="col">Canário</th>
                  <th scope="col">Dados saem?</th>
                </tr>
              </thead>
              <tbody>
                {perfis.map((p) => (
                  <tr key={p.name}>
                    <th scope="row">
                      <span className="mono">{p.name}</span>
                      {p.note ? <><br /><small className={styles.muted}>{p.note}</small></> : null}
                    </th>
                    <td>
                      {[...p.changes, ...p.adjustments].length
                        ? [...p.changes, ...p.adjustments].map((c) => <div key={c}>{c}</div>)
                        : <span className={styles.muted}>nada (igual ao padrão)</span>}
                    </td>
                    <td>{p.canary ?? <span className={styles.muted}>não</span>}</td>
                    <td>
                      {p.changes.length
                        ? <Badge tone={p.external ? 'warning' : 'success'}>{p.external ? 'Sim' : 'Não'}</Badge>
                        : <span className={styles.muted}>como o padrão</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </PageSection>
      ) : null}
    </>
  );
}
