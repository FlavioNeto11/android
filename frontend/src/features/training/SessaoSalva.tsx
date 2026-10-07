/**
 * 31.120: a sessão de treino já salva (ou descartada) abre em LEITURA: as entradas que a pessoa fez, a proposta que virou
 * o fluxo, de onde a sessão veio (execução, etapa e tentativa, só ids) e o diagnóstico da falha. Antes, depois de salvar,
 * a origem e o diagnóstico ficavam invisíveis. Nada daqui roda nem muda: os dados vêm de `GET /api/training/{id}` (v1.75,
 * v1.77); a persona aparece só como marca (as iniciais), com o nome no rótulo acessível.
 */
import { GraduationCap, RefreshCw, ServerCrash } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { PersonaOnDevice, TrainingSession } from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { EmptyState } from '../../components/EmptyState';
import { PacotesAceitos } from '../../components/PacotesAceitos';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { plural } from '../../lib/format';
import { textoComMarcadores } from '../../lib/marcadores';
import { FluxoNoLivro } from './FluxoNoLivro';
import { OrigemDoTreino } from './OrigemDoTreino';
import { DescricaoEntrada, agruparTeclas, falhaDe } from './TrainingReview';
import styles from './Training.module.css';

const ESTADO: Record<TrainingSession['status'], string> = {
  recording: 'gravando', recorded: 'só gravado', proposed: 'com proposta', saved: 'salvo', discarded: 'descartado',
};

export function SessaoSalva({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [sessao, setSessao] = useState<TrainingSession | null>(null);
  const [falha, setFalha] = useState<{ message: string; hint: string } | null>(null);
  const [tentativa, setTentativa] = useState(0);
  const [pessoas, setPessoas] = useState<PersonaOnDevice[]>([]);

  useEffect(() => {
    let vivo = true;
    setSessao(null);
    setFalha(null);
    api.getTraining(sessionId)
      .then((s) => { if (vivo) setSessao(s); })
      .catch((e) => { if (vivo) setFalha(falhaDe(e)); });
    return () => { vivo = false; };
  }, [sessionId, tentativa]);

  // A marca da persona: ler a lista do aparelho é só leitura e, se falhar, a sessão abre sem a marca.
  const aparelho = sessao?.instance_id;
  useEffect(() => {
    if (!aparelho) return;
    let vivo = true;
    api.instancePersonas(aparelho).then((l) => { if (vivo) setPessoas(l); }).catch(() => {});
    return () => { vivo = false; };
  }, [aparelho]);

  const persona = sessao?.profile_id ? pessoas.find((p) => p.profile_id === sessao.profile_id) : undefined;
  const nome = persona ? persona.display_name || persona.name : null;
  // 31.189: a cópia mascarada (v1.109) é a que se exibe; sem ela (backend anterior), a proposta de sempre.
  const proposta = sessao?.proposal_exibicao ?? sessao?.proposal ?? null;
  const entradas = sessao?.inputs ?? [];
  const descartadas = new Set((proposta?.discarded ?? []).map((d) => d.seq));

  return (
    <Dialog open onClose={onClose} title={sessao ? `Treinamento ${sessao.status === 'discarded' ? 'descartado' : 'salvo'}: ${sessao.intent}` : 'Treinamento'}
            icon={GraduationCap} size="lg" footer={<Button onClick={onClose}>Fechar</Button>}>
      {!sessao ? (
        falha ? (
          <EmptyState icon={ServerCrash} tone="danger" compact title="Não foi possível abrir o treinamento" hint={falha.hint}
                      actions={<Button variant="outline" icon={RefreshCw} onClick={() => setTentativa((t) => t + 1)}>Tentar de novo</Button>}>
            {falha.message}
          </EmptyState>
        ) : (
          <LoadingRegion label="Carregando o treinamento…">
            <Skeleton height={120} radius={8} />
          </LoadingRegion>
        )
      ) : (
        <div className={styles.leitura}>
          <p className={styles.recLine}>
            <Badge size="sm">{ESTADO[sessao.status]}</Badge>
            {nome ? <span className={styles.marca} role="img" aria-label={`Persona: ${nome}`} title={nome}><Avatar name={nome} size={20} /></span> : null}
            <span>em <span className="mono">{sessao.instance_id}</span></span>
            {sessao.flow_id ? <span>· fluxo <span className="mono">{sessao.flow_id}</span></span> : null}
          </p>
          <p className={styles.hint}>Só leitura: nada daqui roda nem muda.</p>
          {sessao.flow_id ? <FluxoNoLivro flowId={sessao.flow_id} /> : null}

          {sessao.origin ? <OrigemDoTreino origin={sessao.origin} /> : null}

          <h4 className={styles.sub}>O que você fez ({plural(entradas.length, 'entrada', 'entradas')})</h4>
          {entradas.length ? (
            <ol className={styles.inputList} aria-label="Entradas gravadas">
              {agruparTeclas(entradas, descartadas).map((g) => {
                const e = g[0]!;
                const fim = g[g.length - 1]!;
                return (
                  <li key={e.seq} className={descartadas.has(e.seq) ? styles.discarded : undefined}>
                    <span className={styles.seq}>{g.length > 1 ? `#${e.seq}–#${fim.seq}` : `#${e.seq}`}</span>
                    <span>
                      <DescricaoEntrada e={e} />
                      {g.length > 1 ? <> ×{g.length}</> : null}
                      {e.screen_title ? <span className={styles.muted}> · tela {e.screen_title}</span> : null}
                    </span>
                  </li>
                );
              })}
            </ol>
          ) : <p className={styles.hint}>Nada foi gravado nesta sessão.</p>}

          <h4 className={styles.sub}>A proposta</h4>
          {proposta ? (
            <div className={styles.leituraProposta}>
              <p>{proposta.summary}</p>
              <p className={styles.hint}>Comando modelo: <span className="mono">{proposta.command_template}</span></p>
              {proposta.parameters.length ? (
                <ul className={styles.entryList} aria-label="Parâmetros">
                  {proposta.parameters.map((p) => (
                    <li key={p.name}><span className="mono">{`{${p.name}}`}</span><span className={styles.muted}> exemplo “{p.example}” · {p.description}</span></li>
                  ))}
                </ul>
              ) : null}
              <ol className={styles.entryList} aria-label="Etapas da proposta">
                {proposta.steps.map((s) => (
                  <li key={s.key}>
                    <strong>{s.title}</strong>
                    <span className={styles.muted}>
                      {s.inputs.length ? ` · ${s.inputs.map((n) => `#${n}`).join(', ')}` : ''}
                      {s.side_effect ? ' · com efeito fora do sistema' : ''}
                      {s.postcondition?.description ? ` · confere: ${textoComMarcadores(s.postcondition.description)}` : ''}
                    </span>
                    <PacotesAceitos pacotes={s.pacotes_aceitos} />
                  </li>
                ))}
              </ol>
              {proposta.discarded.length ? (
                <p className={styles.hint}>{plural(proposta.discarded.length, 'entrada descartada', 'entradas descartadas')} da proposta.</p>
              ) : null}
            </div>
          ) : <p className={styles.hint}>Esta sessão não chegou a ter proposta.</p>}
        </div>
      )}
    </Dialog>
  );
}
