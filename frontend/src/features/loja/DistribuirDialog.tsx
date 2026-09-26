/**
 * Distribuir uma versão promovida: para TODOS, para N aparelhos ou para os ESCOLHIDOS — sempre com prévia.
 *
 * A prévia é o mesmo julgamento do backend (`distribute` com `dry_run`), aparelho por aparelho: quem instala já,
 * quem fica pendente e por quê, quem não roda a versão. A tela não reimplementa regra nenhuma. Confirmar manda os
 * MESMOS aparelhos da prévia (em "N aparelhos", os ids que o backend escolheu), para o que se viu ser o que acontece.
 */
import { Eye, Send } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { AppRelease, DistributeDevice, ReleaseTarget } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Checkbox, TextInput } from '../../components/Field';
import { Switch } from '../../components/Switch';
import type { Tone } from '../../lib/status';
import { toast, toastError } from '../../store/toasts';
import styles from './Loja.module.css';

type Modo = 'todos' | 'quantos' | 'escolher';

export const PREVIA: Record<string, { rotulo: string; tom: Tone }> = {
  would_start: { rotulo: 'instala agora', tom: 'info' },
  started: { rotulo: 'instalando', tom: 'info' },
  pending: { rotulo: 'pendente', tom: 'neutral' },
  already: { rotulo: 'já tem', tom: 'success' },
  incompatible: { rotulo: 'não roda aqui', tom: 'warning' },
  kept: { rotulo: 'fica como está', tom: 'neutral' },
};

interface Props {
  release: AppRelease | null;
  nome: string;
  /** Aparelhos já marcados ao abrir ("atualizar os atrasados", "os selecionados na tabela"). */
  preSelecao?: string[];
  onClose: () => void;
  onDone: (devices: DistributeDevice[]) => void;
}

export function DistribuirDialog({ release, nome, preSelecao, onClose, onDone }: Props) {
  const [modo, setModo] = useState<Modo>(preSelecao?.length ? 'escolher' : 'todos');
  const [quantos, setQuantos] = useState(1);
  const [alvos, setAlvos] = useState<ReleaseTarget[] | null>(null);
  const [escolhidos, setEscolhidos] = useState<Set<string>>(new Set(preSelecao ?? []));
  const [agora, setAgora] = useState(false);
  const [previa, setPrevia] = useState<DistributeDevice[] | null>(null);
  const [ocupado, setOcupado] = useState(false);

  useEffect(() => {
    if (!release) return;
    setModo(preSelecao?.length ? 'escolher' : 'todos');
    setEscolhidos(new Set(preSelecao ?? []));
    setPrevia(null);
    setAlvos(null);
    api.releaseTargets(release.id).then((r) => setAlvos(r.targets ?? [])).catch((e) => {
      setAlvos([]);
      toastError('Não foi possível listar os aparelhos', e);
    });
  }, [release, preSelecao]);

  // Qualquer mudança no pedido invalida a prévia: confirmar o que não se viu é o que esta tela existe para evitar.
  useEffect(() => setPrevia(null), [modo, quantos, escolhidos, agora]);

  const alvo = useMemo(() => {
    if (modo === 'escolher') return { instance_ids: [...escolhidos] };
    if (modo === 'quantos') return { count: quantos };
    return {};
  }, [modo, escolhidos, quantos]);

  if (!release) return null;
  const semEscolha = modo === 'escolher' && escolhidos.size === 0;

  async function verPrevia() {
    if (!release) return;
    setOcupado(true);
    try {
      const r = await api.releaseLifecycle(release.id, { verb: 'distribute', dry_run: true, eager: agora, ...alvo });
      setPrevia(r.devices ?? []);
    } catch (e) {
      toastError('A prévia foi recusada', e);
    } finally {
      setOcupado(false);
    }
  }

  async function confirmar() {
    if (!release || !previa) return;
    // "Todos" é o parque inteiro na hora de confirmar; nos outros modos vão exatamente os aparelhos da prévia.
    const ids = previa.filter((d) => d.outcome !== 'incompatible' && d.outcome !== 'already').map((d) => d.id);
    setOcupado(true);
    try {
      const r = await api.releaseLifecycle(release.id, {
        verb: 'distribute', eager: agora, ...(modo === 'todos' ? {} : { instance_ids: ids }) });
      const devices = r.devices ?? [];
      const iniciados = devices.filter((d) => d.outcome === 'started').length;
      toast({
        tone: 'info', title: `${nome} ${release.version_name} distribuída`,
        // Aceito não é instalado: cada aparelho tem o próprio desfecho, que chega sozinho na lista de aparelhos.
        message: `${iniciados} instalando agora, ${devices.filter((d) => d.outcome === 'pending').length} pendente(s). `
          + 'O desfecho de cada aparelho aparece na tabela, sem recarregar.',
      });
      onDone(devices);
      onClose();
    } catch (e) {
      toastError('A distribuição foi recusada', e);
    } finally {
      setOcupado(false);
    }
  }

  const contagem = (o: string) => previa?.filter((d) => d.outcome === o).length ?? 0;
  const acionaveis = previa ? previa.filter((d) => d.outcome !== 'incompatible' && d.outcome !== 'already').length : 0;

  return (
    <Dialog open onClose={onClose} size="lg" icon={Send} title={`Distribuir ${nome} ${release.version_name}`}
            footer={<>
              <Button variant="ghost" onClick={onClose}>Cancelar</Button>
              <Button icon={Eye} loading={ocupado && !previa} onClick={() => void verPrevia()}
                      disabledReason={semEscolha ? 'Escolha ao menos um aparelho.' : null}>Ver prévia</Button>
              <Button variant="primary" icon={Send} loading={ocupado && !!previa} onClick={() => void confirmar()}
                      disabledReason={!previa ? 'Veja a prévia antes de confirmar.'
                        : acionaveis === 0 ? 'Nenhum aparelho da prévia receberia a versão.' : null}>
                Confirmar para {acionaveis} aparelho(s)
              </Button>
            </>}>
      <div className={styles.stack}>
        <div className={styles.modes} role="radiogroup" aria-label="Para quem distribuir">
          {([['todos', 'Todos os aparelhos'], ['quantos', 'N aparelhos'], ['escolher', 'Escolher aparelhos']] as const)
            .map(([m, rotulo]) => (
              <button key={m} type="button" role="radio" aria-checked={modo === m}
                      className={`${styles.chip} ${modo === m ? styles.chipOn : ''}`} onClick={() => setModo(m)}>
                {rotulo}
              </button>
            ))}
        </div>

        {modo === 'todos' ? (
          <p className={styles.muted}>Todo aparelho de tarefa do parque (a loja não entra). Quem não roda esta versão
            fica de fora, com o motivo.</p>
        ) : null}
        {modo === 'quantos' ? (
          <div className={styles.line}>
            <label htmlFor="dist-quantos">Quantos aparelhos:</label>
            <TextInput id="dist-quantos" type="number" min={1} max={200} value={quantos} small
                       onChange={(e) => setQuantos(Math.max(1, Math.min(200, Number(e.target.value) || 1)))} />
            <span className={styles.muted}>O backend escolhe entre os que podem receber e ainda não estão nesta
              versão: ligados primeiro, e quem já tem o app numa versão antiga antes de quem nunca teve.</span>
          </div>
        ) : null}
        {modo === 'escolher' ? (
          alvos === null ? <p className={styles.muted}>Carregando os aparelhos…</p> : (
            <ul className={styles.targets} aria-label="Aparelhos">
              {alvos.map((t) => (
                <li key={t.id}>
                  <Checkbox
                    label={t.id}
                    checked={escolhidos.has(t.id)}
                    disabled={!t.compatible}
                    onChange={(e) => {
                      const novo = new Set(escolhidos);
                      if (e.target.checked) novo.add(t.id); else novo.delete(t.id);
                      setEscolhidos(novo);
                    }} />
                  <Badge size="sm" tone={t.state === 'online' ? 'success' : 'neutral'}>{t.state}</Badge>
                  {t.worker_id ? <Badge size="sm" tone="muted">{t.worker_id}</Badge> : null}
                  {t.already ? <Badge size="sm" tone="success">já tem esta versão</Badge>
                    : t.installed_version_name ? <span className={styles.muted}>tem {t.installed_version_name}</span> : null}
                  {!t.compatible ? <span className={styles.muted}>não roda aqui: {t.reason}</span> : null}
                </li>
              ))}
            </ul>
          )
        ) : null}

        <Switch checked={agora} onChange={setAgora} label="Ligar os desligados agora"
                onText="O rodízio liga os aparelhos desligados desta máquina para instalar"
                offText="Desligados recebem quando ligarem" />

        {previa ? (
          <div>
            <div className={styles.summary}>
              {(['would_start', 'pending', 'already', 'incompatible'] as const).map((o) => contagem(o) ? (
                <Badge key={o} tone={PREVIA[o]!.tom}>{contagem(o)} {PREVIA[o]!.rotulo}</Badge>) : null)}
            </div>
            {previa.length === 0 ? <Banner tone="warning" icon={Send} compact title="Nenhum aparelho">Ninguém caberia neste
              pedido.</Banner> : (
              <ul className={styles.preview} aria-label="Prévia por aparelho">
                {previa.map((d) => (
                  <li key={d.id}>
                    <strong>{d.id}</strong>
                    <Badge size="sm" tone={PREVIA[d.outcome]?.tom ?? 'neutral'}>{PREVIA[d.outcome]?.rotulo ?? d.outcome}</Badge>
                    <span className={styles.muted}>{d.reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <p className={styles.muted}>Veja a prévia: é o backend dizendo, aparelho por aparelho, o que vai acontecer.
            Nada é gravado nem instalado até você confirmar.</p>
        )}
      </div>
    </Dialog>
  );
}
