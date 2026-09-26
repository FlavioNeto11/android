/**
 * Loja de aplicativos (pedido do dono, 26/09): uma vitrine no jeito da Play Store, mas para o PARQUE.
 *
 * Cada cartão é um app cadastrado (Instagram, Outlook, TikTok, uma VPN…) com o ícone lido do APK, a versão
 * promovida, quantos aparelhos têm o app e quantos estão numa versão anterior ("atualização para N"). Abrir o
 * cartão leva à página do app: versões, distribuição com prévia e atualização. "Novo aplicativo" cadastra o pacote;
 * a versão entra depois, pelo arquivo que o dono fornece ou pela Play Store da loja — nunca de espelho.
 */
import { Plus, Store } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { AppCategory, AppStoreEntry } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select, TextInput } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { AppNaLoja } from './AppNaLoja';
import { CATEGORIAS, IconeDoApp } from './comum';
import styles from './Loja.module.css';

type Filtro = AppCategory | 'todas' | 'sem';

export function LojaPage() {
  const [apps, setApps] = useState<AppStoreEntry[] | null>(null);
  const [aberto, setAberto] = useState<string | null>(null);
  const [filtro, setFiltro] = useState<Filtro>('todas');
  const [busca, setBusca] = useState('');
  const [novo, setNovo] = useState(false);
  // O cadastro (inclusive o automático, ao importar a primeira versão de um pacote) chega como `apps.updated`.
  const registro = useAppStore((s) => s.apps);

  const carregar = useCallback(async () => {
    try {
      setApps(await api.appStore());
    } catch (e) {
      setApps((atual) => atual ?? []);
      toastError('Não foi possível carregar a loja', e);
    }
  }, []);

  useEffect(() => { void carregar(); }, [carregar, registro]);

  const visiveis = useMemo(() => (apps ?? []).filter((a) => {
    if (filtro === 'sem' ? a.category !== null : filtro !== 'todas' && a.category !== filtro) return false;
    const q = busca.trim().toLowerCase();
    return !q || a.name.toLowerCase().includes(q) || a.package.toLowerCase().includes(q);
  }), [apps, filtro, busca]);

  const atual = aberto ? apps?.find((a) => a.app_id === aberto) : null;
  if (atual) {
    return <AppNaLoja entry={atual} onBack={() => { setAberto(null); void carregar(); }} onChanged={() => void carregar()} />;
  }
  if (apps === null) {
    return <LoadingRegion label="Carregando a loja…"><Skeleton height={200} /></LoadingRegion>;
  }

  return (
    <div>
      <div className={styles.toolbar}>
        <TextInput aria-label="Buscar aplicativo" placeholder="Buscar por nome ou pacote" value={busca}
                   onChange={(e) => setBusca(e.target.value)} />
        <span className={styles.grow} />
        <Button variant="primary" icon={Plus} onClick={() => setNovo(true)}>Novo aplicativo</Button>
      </div>
      <div className={styles.chips} role="radiogroup" aria-label="Categoria" style={{ marginTop: 'var(--sp-3)' }}>
        {([['todas', 'Todas'], ...Object.entries(CATEGORIAS), ['sem', 'Sem categoria']] as [Filtro, string][])
          .map(([k, v]) => (
            <button key={k} type="button" role="radio" aria-checked={filtro === k}
                    className={`${styles.chip} ${filtro === k ? styles.chipOn : ''}`} onClick={() => setFiltro(k)}>{v}</button>
          ))}
      </div>

      {visiveis.length === 0 ? (
        <EmptyState icon={Store} title={apps.length ? 'Nenhum app neste filtro' : 'Nenhum aplicativo cadastrado'}
                    hint="Cadastre um app novo, ou envie a versão dele: o app é cadastrado sozinho a partir do APK.">
          Nada aqui.
        </EmptyState>
      ) : (
        <div className={styles.grid}>
          {visiveis.map((a) => <CartaoDoApp key={a.app_id} app={a} onOpen={() => setAberto(a.app_id)} />)}
        </div>
      )}

      <NovoAppDialog open={novo} onClose={() => setNovo(false)}
                     onCreated={async (id) => { setNovo(false); await carregar(); setAberto(id); }} />
    </div>
  );
}

function CartaoDoApp({ app, onOpen }: { app: AppStoreEntry; onOpen: () => void }) {
  return (
    <button type="button" className={styles.card} onClick={onOpen} aria-label={`Abrir ${app.name}`}>
      <div className={styles.cardHead}>
        <IconeDoApp entry={app} />
        <div className={styles.title}>
          <strong>{app.name}</strong>
          <code>{app.package}</code>
          {app.category ? <span className={styles.muted}>{CATEGORIAS[app.category]}</span> : null}
        </div>
      </div>
      <div className={styles.line}>
        {app.promoted ? <Badge size="sm" tone="success">{app.promoted.version_name}</Badge>
          : <Badge size="sm" tone="warning">sem versão promovida</Badge>}
        <span>{app.devices_with_app} aparelho(s) com o app</span>
      </div>
      <div className={styles.badges}>
        {app.outdated ? <Badge size="sm" tone="info">atualização para {app.outdated}</Badge> : null}
        {app.pending ? <Badge size="sm" tone="neutral">{app.pending} pendente(s)</Badge> : null}
        {app.installing ? <Badge size="sm" tone="info">{app.installing} instalando</Badge> : null}
        {app.failed ? <Badge size="sm" tone="danger">{app.failed} com falha</Badge> : null}
        {app.other_version ? <Badge size="sm" tone="warning">{app.other_version} fora do catálogo</Badge> : null}
      </div>
      {app.attention[0] ? <span className={styles.muted}>{app.attention[0]}</span> : null}
    </button>
  );
}

const PACOTE = /^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$/;

function NovoAppDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const [nome, setNome] = useState('');
  const [pacote, setPacote] = useState('');
  const [categoria, setCategoria] = useState<AppCategory | ''>('');
  const [salvando, setSalvando] = useState(false);
  useEffect(() => { if (open) { setNome(''); setPacote(''); setCategoria(''); } }, [open]);
  const pacoteInvalido = pacote.trim() !== '' && !PACOTE.test(pacote.trim());

  async function salvar() {
    setSalvando(true);
    try {
      const app = await api.createApp({ name: nome.trim(), package: pacote.trim(), activity: null, apk_path: null,
                                        nav_hints: null, known_selectors: null, category: categoria || null });
      toast({ tone: 'success', title: `${app.name} cadastrado`,
              message: 'Agora envie o APK ou copie a versão da Play Store da loja.' });
      onCreated(app.id);
    } catch (e) {
      toastError('Não foi possível cadastrar', e);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} size="md" icon={Plus} title="Novo aplicativo"
            footer={<>
              <Button variant="ghost" onClick={onClose}>Cancelar</Button>
              <Button variant="primary" loading={salvando} onClick={() => void salvar()}
                      disabledReason={!nome.trim() ? 'Dê um nome.' : !pacote.trim() || pacoteInvalido ? 'Informe o pacote.' : null}>
                Cadastrar</Button>
            </>}>
      <div className={styles.stack}>
        <Field label="Nome">{({ id }) => <TextInput id={id} value={nome} maxLength={80} placeholder="ex.: Outlook"
                                                     onChange={(e) => setNome(e.target.value)} />}</Field>
        <Field label="Pacote" hint="O identificador Android, como aparece na Play Store (…?id=com.microsoft.office.outlook)."
               error={pacoteInvalido ? 'Pacote inválido.' : null}>
          {({ id, describedBy }) => <TextInput id={id} aria-describedby={describedBy} mono value={pacote} maxLength={200}
                                               placeholder="com.microsoft.office.outlook"
                                               onChange={(e) => setPacote(e.target.value)} />}
        </Field>
        <Field label="Categoria">
          {({ id }) => (
            <Select id={id} value={categoria} onChange={(e) => setCategoria(e.target.value as AppCategory | '')}>
              <option value="">Sem categoria</option>
              {Object.entries(CATEGORIAS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          )}
        </Field>
        <p className={styles.muted}>Cadastrar não instala nada. A versão entra pelo APK que você fornecer ou pela Play
          Store da loja, com a sua conta; login e senha no app continuam sendo seus.</p>
      </div>
    </Dialog>
  );
}
