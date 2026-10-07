import { FlaskConical } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { AppConfig } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { Field, Select } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { plural } from '../../lib/format';
import { type LoadError, LoadErrorState, toLoadError } from '../../lib/loadError';
import { alcanceDaPersona, apiAlcance, motivoConhecido, MOTIVO_EM_PALAVRAS, type AlcanceDoApp, type ItemDoAlcance } from './alcance';
import type { Pessoa } from './pessoa';
import styles from './Profiles.module.css';

/**
 * 31.186: o que esta persona pode usar num app, receita a receita e fluxo a fluxo, e por que o que não pode. Só leitura, sem IA:
 * `GET /api/aprendizado/alcance?app=` (adendo v1.107). A regra é a do executor; a tela só a lê e a diz em palavras.
 */

const ORIGEM: Record<string, string> = { ensino: 'ensinada', execucao: 'aprendida de execução' };

function detalheDoItem(i: ItemDoAlcance): string {
  const partes: string[] = [];
  if (i.origem) partes.push(ORIGEM[i.origem] ?? i.origem);
  if (i.tipo === 'receita' && i.reproducoesOk !== null) partes.push(`${plural(i.reproducoesOk, 'reprodução certa', 'reproduções certas')}${i.reproducoesFalha ? `, ${plural(i.reproducoesFalha, 'falha', 'falhas')}` : ''}`);
  if (i.tipo === 'fluxo' && i.usos !== null) partes.push(plural(i.usos, 'uso', 'usos'));
  return partes.join(' · ');
}

function Item({ item }: { item: ItemDoAlcance }) {
  return (
    <li className={styles.policyRow} data-item={item.id}>
      <span className={styles.policyRowTitle}>
        <Badge size="sm" tone={item.tipo === 'receita' ? 'neutral' : 'accent'}>{item.tipo === 'receita' ? 'Receita' : 'Fluxo'}</Badge>
        <code>{item.chave}</code>
        {item.nascidoDeProva ? <Badge size="sm" tone="warning">nascido de prova</Badge> : null}
      </span>
      <span className={styles.muted}>{detalheDoItem(item)}</span>
    </li>
  );
}

export function AlcanceDaPersona({ profile }: { profile: Pessoa }) {
  const [apps, setApps] = useState<AppConfig[] | null>(null);
  const [appId, setAppId] = useState('');
  const [dado, setDado] = useState<AlcanceDoApp | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [vez, setVez] = useState(0);

  useEffect(() => {
    let vivo = true;
    api.listApps().then((a) => { if (vivo) { setApps(a); setAppId((atual) => atual || a.find((x) => x.id === 'instagram')?.id || a[0]?.id || ''); } })
      .catch((e: unknown) => { if (vivo) setErro(toLoadError(e)); });
    return () => { vivo = false; };
  }, []);

  useEffect(() => {
    if (!appId) return;
    const ctl = new AbortController();
    setDado(null);
    setErro(null);
    apiAlcance.doApp(appId, profile.id, ctl.signal).then((d) => setDado(d)).catch((e: unknown) => { if (!ctl.signal.aborted) setErro(toLoadError(e)); });
    return () => ctl.abort();
  }, [appId, profile.id, vez]);

  const a = useMemo(() => (dado ? alcanceDaPersona(dado, profile.id) : null), [dado, profile.id]);
  const gruposDoNao = useMemo(() => {
    const por = new Map<string, NonNullable<typeof a>['nao']>();
    for (const x of a?.nao ?? []) por.set(x.motivo ?? '', [...(por.get(x.motivo ?? '') ?? []), x]);
    return [...por.entries()];
  }, [a]);

  return (
    <Card>
      <CardHeader title="O que esta persona pode usar" subtitle="As receitas e os fluxos de um app, e por que alguns não valem para ela. Só leitura: confirmar ou ligar é no Aprendizado." />
      <CardBody className={styles.form}>
        <Field label="App">
          {({ id }) => (
            <Select id={id} small value={appId} disabled={!apps} onChange={(e) => setAppId(e.target.value)}>
              {(apps ?? []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </Select>
          )}
        </Field>
        {erro ? <LoadErrorState what="o alcance da persona" error={erro} onRetry={() => setVez((n) => n + 1)} /> : null}
        {!erro && !dado ? <LoadingRegion label="Lendo o alcance"><Skeleton height={60} /></LoadingRegion> : null}
        {dado?.exemplo ? (
          <Banner tone="info" icon={FlaskConical} compact role="status" title="Dados de exemplo">
            O central ainda não oferece o alcance por persona: esta lista é um exemplo inventado. Nada aqui veio do parque.
          </Banner>
        ) : null}
        {a && dado && !a.vinculada ? (
          <p className={styles.muted} role="status">Esta persona não tem vínculo ativo com este app, então o central não calcula o alcance dela.</p>
        ) : null}
        {a && a.vinculada ? (
          <>
            <p role="status" data-resumo>
              Pode usar <strong>{plural(a.pode.filter((i) => i.tipo === 'receita').length, 'receita', 'receitas')}</strong> e{' '}
              <strong>{plural(a.pode.filter((i) => i.tipo === 'fluxo').length, 'fluxo', 'fluxos')}</strong>; {plural(a.nao.length, 'não vale', 'não valem')} para ela
              {a.semResposta > 0 ? `; ${plural(a.semResposta, 'item sem resposta do central', 'itens sem resposta do central')} não entram na conta` : ''}.
            </p>
            {a.pode.length ? (
              <section aria-label="Pode usar">
                <h3 className={styles.muted}>Pode usar ({a.pode.length})</h3>
                <ul className={styles.list}>{a.pode.map((i) => <Item key={i.id} item={i} />)}</ul>
              </section>
            ) : null}
            {gruposDoNao.map(([motivo, itens]) => {
              const m = motivoConhecido(motivo) ? MOTIVO_EM_PALAVRAS[motivo] : { titulo: 'Não vale, sem motivo descrito', explica: motivo ? `O central deu o motivo “${motivo}”, que esta tela ainda não descreve.` : 'O central não disse o motivo.' };
              return (
                <section key={motivo} aria-label={m.titulo} data-motivo={motivo || 'sem_motivo'}>
                  <h3 className={styles.muted}>{m.titulo} ({itens.length})</h3>
                  <p className={styles.muted}>{m.explica}</p>
                  <ul className={styles.list}>{itens.map((x) => <Item key={x.item.id} item={x.item} />)}</ul>
                </section>
              );
            })}
            {a.pode.length + a.nao.length === 0 ? <p className={styles.muted}>Este app não tem receita ativa nem fluxo ligado ou candidato.</p> : null}
          </>
        ) : null}
      </CardBody>
    </Card>
  );
}
