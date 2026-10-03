import { ExternalLink, Plus, RefreshCw, Save, ServerCrash, Wallet } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { AiBalance, AiBalancesReport } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { Field, TextInput } from '../../components/Field';
import { KvList, KvRow } from '../../components/JsonTree';
import { PageSection } from '../../components/Page';
import {
  balanceAge, balanceSourceLabel, balanceStateLabel, balanceTone, balanceUsage, consumptionUsd, money,
} from '../../lib/aiBalance';
import { toast, toastError } from '../../store/toasts';
import styles from './AiBalances.module.css';

/**
 * Saldo das contas de IA (ADR-051). Nenhum console publica o saldo por API: o painel guarda a última leitura e
 * desconta o gasto registrado desde ela. Por isso a leitura nova é o gesto principal — e o link do console fica
 * ao lado, para conferir antes de digitar.
 */
export function AiBalances() {
  const [report, setReport] = useState<AiBalancesReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    try {
      setReport(await api.aiBalances(refresh));
      setError(null);
    } catch (e) {
      setError(toApiError(e).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <PageSection
      title={<><Wallet size={16} aria-hidden className={styles.inlineIcon} /> Saldo das contas</>}
      subtitle="Livro-caixa: saldo = âncora menos o consumo desde ela. O consumo vem do relatório oficial do provedor (Anthropic por hora, OpenAI por dia) e de cada chamada da plataforma; a cada 24 h o dia fecha sozinho. Você só registra as recargas. Abaixo do bloqueio, a IA daquela conta para (ou cai no fallback declarado)."
      actions={<Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={() => void load(true)}>Conciliar agora</Button>}
    >
      {error ? <Banner tone="warning" icon={ServerCrash} compact title="Não foi possível consultar os saldos">{error}</Banner> : null}
      <div className={styles.grid}>
        {(report?.accounts ?? []).map((b) => <BalanceCard key={b.account} b={b} onSaved={setReport} />)}
      </div>
      {report ? <p className={styles.note}>{report.note}</p> : null}
    </PageSection>
  );
}

/** De onde vem o consumo desta conta, e como está a conciliação, em uma linha. */
function consumptionLabel(b: AiBalance): string {
  if (b.account === 'gemini') return 'Medido em cada chamada da plataforma (o Google não publica consumo por API)';
  if (!b.admin_key_configured) return 'Só o consumo da plataforma (sem chave de administrador no backend)';
  const fonte = b.account === 'anthropic' ? 'relatório oficial de uso, por hora' : 'relatório oficial de custo, por dia';
  if (b.reconcile_error) return `${fonte} — falhou: ${b.reconcile_error}`;
  if (b.provider_usd === null || b.provider_usd === undefined) return `${fonte} — aguardando a primeira consulta`;
  const fora = b.external_usd > 0 ? `${money(b.external_usd, 'USD')} fora da plataforma` : 'nada fora da plataforma';
  return `${fonte} · ${fora}`;
}

function num(v: string): number | null {
  const t = v.trim().replace(/\s/g, '').replace(',', '.');
  if (t === '') return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : NaN;
}

function BalanceCard({ b, onSaved }: { b: AiBalance; onSaved: (r: AiBalancesReport) => void }) {
  const [valor, setValor] = useState('');
  const [recarga, setRecarga] = useState('');
  const [aviso, setAviso] = useState(b.warn_below === null ? '' : String(b.warn_below));
  const [bloqueio, setBloqueio] = useState(b.block_below === null ? '' : String(b.block_below));
  const [salvando, setSalvando] = useState<'leitura' | 'recarga' | 'regra' | null>(null);
  const tone = balanceTone(b);

  const registrar = async () => {
    const n = num(valor);
    if (n === null || Number.isNaN(n)) return;
    setSalvando('leitura');
    try {
      onSaved(await api.aiBalanceReading(b.account, { balance: n, source: 'console' }));
      setValor('');
      toast({ tone: 'success', title: `Saldo de ${b.label} registrado: ${money(n, b.currency)}` });
    } catch (e) {
      toastError('Não foi possível registrar o saldo', e);
    } finally {
      setSalvando(null);
    }
  };

  const recarregar = async () => {
    const n = num(recarga);
    if (n === null || Number.isNaN(n) || n <= 0) return;
    setSalvando('recarga');
    try {
      onSaved(await api.aiBalanceRecharge(b.account, { amount: n }));
      setRecarga('');
      toast({ tone: 'success', title: `Recarga de ${money(n, b.currency)} registrada em ${b.label}` });
    } catch (e) {
      toastError('Não foi possível registrar a recarga', e);
    } finally {
      setSalvando(null);
    }
  };

  const salvarRegra = async () => {
    const w = num(aviso);
    const k = num(bloqueio);
    if (Number.isNaN(w) || Number.isNaN(k)) return;
    setSalvando('regra');
    try {
      onSaved(await api.aiBalanceRule(b.account, { warn_below: w, block_below: k }));
      toast({ tone: 'success', title: `Limites de ${b.label} salvos` });
    } catch (e) {
      toastError('Não foi possível salvar os limites', e);
    } finally {
      setSalvando(null);
    }
  };

  const valorInvalido = valor.trim() !== '' && Number.isNaN(num(valor));
  const recargaNum = num(recarga);
  const recargaInvalida = recarga.trim() !== '' && (Number.isNaN(recargaNum) || (recargaNum ?? 0) <= 0);
  const regraInvalida = Number.isNaN(num(aviso)) || Number.isNaN(num(bloqueio));

  return (
    <article className={styles.card} aria-label={`Saldo ${b.label}`} data-tone={tone}>
      <header className={styles.head}>
        <h3 className={styles.title}>{b.label}</h3>
        <Badge tone={tone}>{b.stale && b.state === 'ok' ? 'Sem conciliação' : balanceStateLabel(b.state)}</Badge>
      </header>
      <p className={styles.amount}>
        {money(b.estimated_balance, b.currency)}
        {b.currency !== 'USD' && b.estimated_balance_usd !== null
          ? <small className={styles.muted}> ≈ {money(b.estimated_balance_usd, 'USD')}</small> : null}
      </p>
      <p className={styles.message}>{b.message}</p>
      <KvList>
        <KvRow label="Usada por">{balanceUsage(b)}</KvRow>
        <KvRow label="Chave no backend">{b.key_configured ? 'Presente' : 'Ausente'}</KvRow>
        <KvRow label="Âncora">
          {b.anchor_balance === null ? '—'
            : `${money(b.anchor_balance, b.currency)} · ${balanceAge(b.age_h)} · ${balanceSourceLabel(b.anchor_source)}`}
        </KvRow>
        <KvRow label="Consumo desde a âncora">{consumptionUsd(b.spent_since_usd + b.external_usd)}</KvRow>
        <KvRow label="Consumo vem de">{consumptionLabel(b)}</KvRow>
        {b.currency !== 'USD' ? <KvRow label="Câmbio">{`${b.units_per_usd} ${b.currency} por US$ 1`}</KvRow> : null}
      </KvList>

      <form className={styles.row} onSubmit={(e) => { e.preventDefault(); void recarregar(); }}>
        <Field label="Comprei crédito" unit={b.currency} error={recargaInvalida ? 'Valor inválido' : null}
          hint="Registre cada compra no console; o consumo o livro-caixa já acompanha sozinho.">
          {({ id, describedBy, invalid }) => (
            <TextInput id={id} aria-describedby={describedBy} invalid={invalid} small inputMode="decimal"
              placeholder="ex.: 10,00" value={recarga} onChange={(e) => setRecarga(e.target.value)} />
          )}
        </Field>
        <Button type="submit" size="sm" variant="primary" icon={Plus} loading={salvando === 'recarga'}
          disabledReason={recarga.trim() === '' ? 'Digite o valor comprado' : recargaInvalida ? 'Valor inválido' : null}>
          Registrar recarga
        </Button>
        <a className={styles.console} href={b.console} target="_blank" rel="noreferrer">
          Abrir console <ExternalLink size={12} aria-hidden />
        </a>
      </form>

      <Disclosure bare summary={b.anchor_balance === null ? 'Saldo inicial (uma vez)' : 'Conferir com o console (opcional)'}>
        {() => (
          <form className={styles.row} onSubmit={(e) => { e.preventDefault(); void registrar(); }}>
            <Field label="Saldo que o console mostra" unit={b.currency} error={valorInvalido ? 'Número inválido' : null}>
              {({ id, describedBy, invalid }) => (
                <TextInput id={id} aria-describedby={describedBy} invalid={invalid} small inputMode="decimal"
                  placeholder="ex.: 9,25" value={valor} onChange={(e) => setValor(e.target.value)} />
              )}
            </Field>
            <Button type="submit" size="sm" variant="outline" icon={Save} loading={salvando === 'leitura'}
              disabledReason={valor.trim() === '' ? 'Digite o saldo que o console mostra' : valorInvalido ? 'Número inválido' : null}>
              Corrigir saldo
            </Button>
          </form>
        )}
      </Disclosure>

      <form className={styles.row} onSubmit={(e) => { e.preventDefault(); void salvarRegra(); }}>
        <Field label="Avisar abaixo de" unit={b.currency}>
          {({ id, describedBy }) => (
            <TextInput id={id} aria-describedby={describedBy} small inputMode="decimal" placeholder="sem aviso"
              value={aviso} onChange={(e) => setAviso(e.target.value)} />
          )}
        </Field>
        <Field label="Bloquear abaixo de" unit={b.currency}>
          {({ id, describedBy }) => (
            <TextInput id={id} aria-describedby={describedBy} small inputMode="decimal" placeholder="sem bloqueio"
              value={bloqueio} onChange={(e) => setBloqueio(e.target.value)} />
          )}
        </Field>
        <Button type="submit" size="sm" variant="outline" loading={salvando === 'regra'}
          disabledReason={regraInvalida ? 'Número inválido' : null}>
          Salvar limites
        </Button>
      </form>
    </article>
  );
}
