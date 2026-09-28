/**
 * Proxy do aparelho, distribuído como as versões (decisão do dono, 26/09).
 *
 * Um proxy nomeado é pedido para os aparelhos escolhidos, com prévia. Ligado recebe agora; desligado recebe quando
 * ligar. "Aplicado" quer dizer que o aparelho respondeu, ao ser lido de volta, exatamente o que foi pedido. Isso
 * prova a CONFIGURAÇÃO, não o tráfego: app que ignora o proxy do sistema continua saindo direto.
 */
import { Eye, Globe, Plus, RefreshCw, Send, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { DistributeDevice, ProxyList } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Checkbox, Select, TextInput } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { type LoadError, LoadErrorState, toLoadError } from '../../lib/loadError';
import { useIntervaloVisivel } from '../../lib/polling';
import type { Tone } from '../../lib/status';
import { toast, toastError } from '../../store/toasts';
import { PREVIA } from './DistribuirDialog';
import styles from './Loja.module.css';

const SITUACAO: Record<string, { rotulo: string; tom: Tone }> = {
  pending: { rotulo: 'pedido, ainda não aplicado', tom: 'neutral' },
  applying: { rotulo: 'aplicando', tom: 'info' },
  applied: { rotulo: 'aplicado e conferido', tom: 'success' },
  failed: { rotulo: 'falhou', tom: 'danger' },
};
const SEM_PROXY = '__sem__';

export function ProxyPage() {
  const [dados, setDados] = useState<ProxyList | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [nome, setNome] = useState('');
  const [host, setHost] = useState('');
  const [porta, setPorta] = useState('3128');
  const [escolha, setEscolha] = useState<string>('');
  const [selecionados, setSelecionados] = useState<Set<string>>(new Set());
  const [previa, setPrevia] = useState<DistributeDevice[] | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const token = useRef(0);
  // Espelho de `dados` para o catch decidir sem virar dependência (e recriar a releitura a cada resposta).
  const temDados = useRef(false);

  const carregar = useCallback(async () => {
    const meu = ++token.current;
    try {
      const d = await api.listProxies();
      if (meu !== token.current) return;
      setDados(d);
      temDados.current = true;
      setErro(null);
    } catch (e) {
      if (meu !== token.current) return;
      // Sem dado, o erro ocupa a tela com "Tentar de novo" (antes virava tabela vazia). Com dado, um toast só:
      // a releitura de 3 s repetia o mesmo erro em pilha; a chave faz o novo substituir o anterior (P3.4).
      setErro(toLoadError(e));
      if (temDados.current) toastError('Não foi possível carregar os proxies', e, { key: 'proxies-carregar' });
    }
  }, []);

  useEffect(() => { void carregar(); }, [carregar]);
  // Enquanto algum aparelho está aplicando, relê de tempos em tempos — só com a aba visível: o desfecho vem do
  // aparelho, não do clique, e em segundo plano ninguém está olhando.
  const emAndamento = dados?.devices.some((d) => d.state === 'applying') ?? false;
  useIntervaloVisivel(carregar, 3000, emAndamento);
  useEffect(() => setPrevia(null), [escolha, selecionados]);

  if (!dados) {
    return erro
      ? <LoadErrorState what="os proxies" error={erro} onRetry={() => void carregar()} />
      : <LoadingRegion label="Carregando proxies…"><Skeleton height={200} /></LoadingRegion>;
  }
  const nomeDe = (id: string | null) => (id ? dados.profiles.find((p) => p.id === id)?.name ?? id : 'sem proxy');
  const corpo = () => ({ proxy_id: escolha === SEM_PROXY ? null : escolha, instance_ids: [...selecionados] });

  async function criar() {
    try {
      const p = await api.createProxy({ name: nome.trim(), host: host.trim(), port: Number(porta) });
      toast({ tone: 'success', title: `Proxy ${p.name} criado` });
      setNome(''); setHost('');
      setEscolha(p.id);
      await carregar();
    } catch (e) {
      toastError('Não foi possível criar o proxy', e);
    }
  }

  async function apagar(id: string, rotulo: string) {
    const { confirmed } = await confirm({ title: `Apagar o proxy ${rotulo}?`, danger: true, confirmLabel: 'Apagar',
      body: 'Só apaga o cadastro. Proxy pedido para algum aparelho não pode ser apagado: tire-o antes.' });
    if (!confirmed) return;
    try {
      await api.deleteProxy(id);
      await carregar();
    } catch (e) {
      toastError('Não foi possível apagar', e);
    }
  }

  async function verPrevia() {
    setOcupado(true);
    try {
      setPrevia((await api.applyProxy({ ...corpo(), dry_run: true })).devices);
    } catch (e) {
      toastError('A prévia foi recusada', e);
    } finally {
      setOcupado(false);
    }
  }

  async function aplicar() {
    setOcupado(true);
    try {
      const r = await api.applyProxy(corpo());
      toast({ tone: 'info', title: escolha === SEM_PROXY ? 'Retirada do proxy pedida' : 'Proxy pedido',
              message: `${r.devices.filter((d) => d.outcome === 'started').length} aplicando agora; os desligados `
                + 'recebem quando ligarem. A situação de cada um aparece na tabela.' });
      setPrevia(null);
      await carregar();
    } catch (e) {
      toastError('O pedido foi recusado', e);
    } finally {
      setOcupado(false);
    }
  }

  const todos = dados.devices.length > 0 && selecionados.size === dados.devices.length;
  const semAlvo = !escolha ? 'Escolha o proxy (ou "sem proxy").' : selecionados.size === 0 ? 'Selecione aparelhos.' : null;

  return (
    <div className={styles.stack}>
      <Banner tone="info" icon={Globe} compact title="O que o proxy prova aqui">
        O aparelho é configurado com o proxy HTTP global do Android e a configuração é lida de volta. Isso prova que o
        aparelho aceitou o proxy, não que todo app o use. Proxy com usuário e senha não é suportado: o Android não
        tem esse campo, e senha não se guarda em texto.
      </Banner>

      <Card>
        <CardHeader title="Proxies" subtitle="Cada um é um host e uma porta, com um nome para escolher na distribuição." />
        <CardBody>
          <div className={styles.form}>
            <TextInput aria-label="Nome do proxy" placeholder="Nome (ex.: Escritório)" value={nome} maxLength={60}
                       onChange={(e) => setNome(e.target.value)} />
            <TextInput aria-label="Host" placeholder="Host ou IP (ex.: 10.0.0.5)" value={host} mono maxLength={253}
                       onChange={(e) => setHost(e.target.value)} />
            <TextInput aria-label="Porta" type="number" min={1} max={65535} value={porta} onChange={(e) => setPorta(e.target.value)} />
            <Button icon={Plus} onClick={() => void criar()}
                    disabledReason={!nome.trim() || !host.trim() || !Number(porta) ? 'Preencha nome, host e porta.' : null}>
              Criar</Button>
          </div>
          {dados.profiles.length === 0 ? <p className={styles.muted}>Nenhum proxy cadastrado.</p> : (
            <ul className={styles.versions} style={{ marginTop: 'var(--sp-3)' }}>
              {dados.profiles.map((p) => (
                <li key={p.id} className={styles.version}>
                  <span className={styles.versionName}>{p.name}</span>
                  <code>{p.host}:{p.port}</code>
                  <span className={styles.muted}>{p.devices} aparelho(s)</span>
                  <span className={styles.grow} />
                  <Button size="sm" variant="dangerGhost" icon={Trash2} iconOnly label={`Apagar ${p.name}`}
                          disabledReason={p.devices ? 'Pedido para algum aparelho: tire-o antes.' : null}
                          onClick={() => void apagar(p.id, p.name)} />
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Aparelhos" subtitle="Escolha o proxy e os aparelhos, veja a prévia e confirme."
                    actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void carregar()}>Recarregar</Button>} />
        <CardBody>
          <div className={styles.bulk}>
            <Select aria-label="Proxy a aplicar" small className={styles.inlineSelect} value={escolha} onChange={(e) => setEscolha(e.target.value)}>
              <option value="">Escolha o proxy…</option>
              {dados.profiles.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.host}:{p.port})</option>)}
              <option value={SEM_PROXY}>Sem proxy (tirar)</option>
            </Select>
            <span className={styles.muted}>{selecionados.size} selecionado(s)</span>
            <Button size="sm" icon={Eye} loading={ocupado && !previa} disabledReason={semAlvo} onClick={() => void verPrevia()}>
              Ver prévia</Button>
            <Button size="sm" variant="primary" icon={Send} loading={ocupado && !!previa}
                    disabledReason={semAlvo ?? (!previa ? 'Veja a prévia antes de confirmar.' : null)}
                    onClick={() => void aplicar()}>Aplicar</Button>
          </div>
          {previa ? (
            <ul className={styles.preview} aria-label="Prévia do proxy">
              {previa.map((d) => (
                <li key={d.id}><strong>{d.id}</strong>
                  <Badge size="sm" tone={PREVIA[d.outcome]?.tom ?? 'neutral'}>{d.outcome === 'would_start' ? 'aplica agora' : PREVIA[d.outcome]?.rotulo ?? d.outcome}</Badge>
                  <span className={styles.muted}>{d.reason}</span></li>
              ))}
            </ul>
          ) : null}
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th><Checkbox aria-label="Selecionar todos" checked={todos}
                                indeterminate={selecionados.size > 0 && !todos}
                                onChange={(e) => setSelecionados(e.target.checked ? new Set(dados.devices.map((d) => d.instance_id)) : new Set())} /></th>
                  <th>Aparelho</th><th>Pedido</th><th>Situação</th><th>O aparelho responde</th>
                </tr>
              </thead>
              <tbody>
                {dados.devices.map((d) => {
                  const s = d.state ? SITUACAO[d.state] : null;
                  return (
                    <tr key={d.instance_id}>
                      <td><Checkbox aria-label={`Selecionar ${d.instance_id}`} checked={selecionados.has(d.instance_id)}
                                    onChange={(e) => {
                                      const novo = new Set(selecionados);
                                      if (e.target.checked) novo.add(d.instance_id); else novo.delete(d.instance_id);
                                      setSelecionados(novo);
                                    }} /></td>
                      <td><strong>{d.instance_id}</strong> <span className={styles.muted}>{d.device_state}{d.worker_id ? ` · ${d.worker_id}` : ''}</span></td>
                      <td>{d.managed ? nomeDe(d.desired_proxy_id) : <span className={styles.muted}>não gerenciado</span>}</td>
                      <td>{s ? <Badge size="sm" tone={s.tom}>{s.rotulo}</Badge> : null}
                        {d.detail ? <div className={styles.muted}>{d.detail}</div> : null}</td>
                      <td>{d.observed_value != null ? <code>{d.observed_value || 'vazio'}</code> : <span className={styles.muted}>—</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
