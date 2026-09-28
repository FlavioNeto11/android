import { BrainCircuit, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { api } from '../../api/client';
import type { MemoryItem } from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Field, TextArea, TextInput } from '../../components/Field';
import { clamp01 } from '../../lib/format';
import { formatAgo, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { Carregando, useLista, useVersaoAoVivo } from './detalheComum';
import type { Pessoa } from './pessoa';
import styles from './Profiles.module.css';

// ---------------------------------------------------------------- memória
/** Agrupa por assunto (a pessoa/tema) e ordena por importância × recência — os fatos que mais importam e
 *  os mais frescos primeiro, tanto dentro de cada cartão quanto na ordem dos cartões. */
function agruparMemoriaPorAssunto(itens: MemoryItem[]): { assunto: string; itens: MemoryItem[] }[] {
  const porAssunto = new Map<string, MemoryItem[]>();
  for (const m of itens) {
    const lista = porAssunto.get(m.subject) ?? [];
    lista.push(m);
    porAssunto.set(m.subject, lista);
  }
  const recencia = (m: MemoryItem) => Date.parse(m.last_used_at ?? m.updated_at ?? m.created_at) || 0;
  const grupos = [...porAssunto.entries()].map(([assunto, lista]) => ({
    assunto,
    itens: [...lista].sort((a, b) => b.importance - a.importance || recencia(b) - recencia(a)),
  }));
  grupos.sort((a, b) => {
    const [topoA] = a.itens;
    const [topoB] = b.itens;
    return (topoB?.importance ?? 0) - (topoA?.importance ?? 0)
      || (topoB ? recencia(topoB) : 0) - (topoA ? recencia(topoA) : 0);
  });
  return grupos;
}

/** De onde veio cada fato: a tela vista não tem o mesmo peso do que a pessoa disse ao perfil. */
const ORIGEM_DA_MEMORIA: Record<string, { label: string; tone: 'info' | 'success' | 'neutral' }> = {
  observation: { label: 'visto na tela', tone: 'info' },
  interaction: { label: 'dito pela pessoa', tone: 'success' },
  operator: { label: 'ensinado', tone: 'neutral' },
  system: { label: 'sistema', tone: 'neutral' },
};

export function AbaMemoria({ profile, appId = null }: { profile: Pessoa; appId?: string | null }) {
  const versao = useVersaoAoVivo(profile);
  const [itens, recarregar] = useLista<MemoryItem>(() => api.listMemory(profile.id, 100, appId), [profile.id, versao, appId]);
  const appsDoStore = useAppStore((st) => st.apps);
  const nomeDoApp = new Map(appsDoStore.map((x) => [x.id, x.name]));
  const [assunto, setAssunto] = useState('');
  const [conteudo, setConteudo] = useState('');
  const [salvando, setSalvando] = useState(false);
  const [ensinando, setEnsinando] = useState(false);
  const now = useNow();

  async function adicionar() {
    setSalvando(true);
    try {
      await api.addMemory(profile.id, { subject: assunto.trim(), content: conteudo.trim(), app_id: appId });
      setAssunto('');
      setConteudo('');
      setEnsinando(false);
      await recarregar();
      toast({ tone: 'success', title: 'Fato guardado' });
    } catch (e) {
      toastError('Não foi possível guardar', e);
    } finally {
      setSalvando(false);
    }
  }

  async function esquecer(item: MemoryItem) {
    if (!(await confirm({ title: 'Esquecer este fato?', body: item.content, confirmLabel: 'Esquecer',
                          danger: true })).confirmed) return;
    try {
      await api.deleteMemory(profile.id, item.id);
      await recarregar();
    } catch (e) {
      toastError('Não foi possível esquecer', e);
    }
  }

  if (itens === null) return <Carregando />;
  const grupos = agruparMemoriaPorAssunto(itens);
  return (
    <Card>
      <CardHeader title="O que este perfil sabe"
                  subtitle="O que ele viu na tela, o que as pessoas disseram a ele e o que foi ensinado aqui. Senha e código nunca entram."
                  actions={
                    <Button size="sm" icon={BrainCircuit} onClick={() => setEnsinando((v) => !v)}>
                      {ensinando ? 'Fechar' : 'Ensinar um fato'}
                    </Button>
                  } />
      <CardBody>
        {ensinando ? (
          <div className={styles.form} style={{ marginBottom: 'var(--sp-4)' }}>
            <Field label="Sobre quem/o quê" hint="Ex.: @ana, ou um tema.">
              {({ id }) => (
                <TextInput id={id} value={assunto} onChange={(e) => setAssunto(e.target.value)} placeholder="@ana" />
              )}
            </Field>
            <Field label="Fato">
              {({ id }) => (
                <TextArea id={id} rows={3} value={conteudo} onChange={(e) => setConteudo(e.target.value)}
                          placeholder="Corre maratonas aos domingos" />
              )}
            </Field>
            <div>
              <Button loading={salvando}
                      disabledReason={assunto.trim() && conteudo.trim() ? null : 'Preencha o assunto e o fato.'}
                      onClick={() => void adicionar()}>Guardar</Button>
            </div>
          </div>
        ) : null}
        {itens.length === 0 ? (
          <p className={styles.detail}>Nenhuma lembrança ainda.</p>
        ) : (
          <div className={styles.memoryGroups}>
            {grupos.map((g) => (
              <div key={g.assunto} className={styles.memoryCard}>
                <div className={styles.memoryCardHead}>
                  <Avatar name={g.assunto} size={32} />
                  <strong>{g.assunto}</strong>
                </div>
                <ul className={styles.memoryFacts}>
                  {g.itens.map((m) => (
                    <li key={m.id} className={styles.memoryFact}>
                      <div className={styles.memoryFactHead}>
                        <span className={styles.importanceBar} title={`Importância ${m.importance.toFixed(1)}`}>
                          <span className={styles.importanceBarFill} style={{ width: `${Math.round(clamp01(m.importance) * 100)}%` }} />
                        </span>
                        <Badge size="sm" tone={m.app_id ? 'accent' : 'muted'}>
                          {m.app_id ? (nomeDoApp.get(m.app_id) ?? m.app_id) : 'geral'}
                        </Badge>
                        <Badge size="sm" tone={ORIGEM_DA_MEMORIA[m.source]?.tone ?? 'neutral'}>
                          {ORIGEM_DA_MEMORIA[m.source]?.label ?? m.source}
                        </Badge>
                        <Badge size="sm" tone={m.confidence >= 0.7 ? 'success' : m.confidence >= 0.4 ? 'warning' : 'muted'}>
                          confiança {Math.round(m.confidence * 100)}%
                        </Badge>
                        <span className={styles.muted}>
                          visto {m.occurrences}x · usado {m.last_used_at ? formatAgo(m.last_used_at, now) : 'nunca'}
                        </span>
                        <Button size="sm" variant="ghost" icon={Trash2} onClick={() => void esquecer(m)}>Esquecer</Button>
                      </div>
                      <p style={{ margin: 0 }}>{m.content}</p>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
