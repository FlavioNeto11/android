import { ArrowRight, CircleHelp, Eraser, Route, TriangleAlert } from 'lucide-react';
import { profileAvatarUrl } from '../../api/client';
import type { AppConfig, ResolveTargetsResponse, TargetQuestion } from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import ui from '../../components/ui.module.css';
import { cx } from '../../lib/format';
import { appsDoAlvo, ORIGEM, type RecusaDeAlvo } from './alvos';
import styles from './CommandPanel.module.css';

/** Como mostrar uma persona pelo id: nome e @ quando a lista de personas já chegou; senão o próprio id. */
export type NomeDaPersona = (profileId: string) => { nome: string; handle: string | null };

/**
 * A prévia dos alvos (`POST /runs/targets/resolve`): persona → aparelho com a ORIGEM de cada escolha, o comando que
 * a IA recebe quando o texto tinha destinos, os avisos e as perguntas, cada opção um botão que preenche a seleção.
 * Mostrada ANTES de executar (design §7.6): destino deduzido do texto nunca executa sem ter passado por aqui.
 * Item 24.6: cada alvo mostra o CONJUNTO de apps exigidos (`app_ids`, contrato C5) — um comando entre apps não
 * força mais "um app" por alvo na tela.
 */
export function PreviaDosAlvos({ previa, recusa, carregando, comando, nomeDe, apps, onResponder, onSemDestinos }: {
  previa: ResolveTargetsResponse | null;
  recusa: RecusaDeAlvo | null;
  carregando: boolean;
  /** O comando como está no campo, para saber se o texto tinha destinos. */
  comando: string;
  nomeDe: NomeDaPersona;
  /** Catálogo de apps carregado, para nomear os `app_ids` do alvo. Vazio = mostra o próprio id. */
  apps?: readonly Pick<AppConfig, 'id' | 'name'>[];
  onResponder: (q: TargetQuestion, opcao: string) => void;
  onSemDestinos: (comando: string) => void;
}) {
  const semDestinos = previa ? previa.command_sem_destinos.trim() : '';
  const textoTinhaDestinos = !!previa && semDestinos !== '' && semDestinos !== comando.trim();
  const rotuloDaOpcao = (q: TargetQuestion, o: string) => (q.field === 'profile_id' ? nomeDe(o).nome : o);
  // Os avisos do backend citam a persona pelo id ("… mais de um aparelho (ig-f0zk…)"): na tela, pelo nome.
  const ids = [...new Set((previa?.targets ?? []).flatMap((t) => (t.profile_id ? [t.profile_id] : [])))];
  const comNomes = (aviso: string) => ids.reduce((texto, id) => texto.split(id).join(nomeDe(id).nome), aviso);

  return (
    <section className={styles.previa} role="region" aria-label="Prévia dos alvos" aria-busy={carregando || undefined}>
      <p className={styles.previaTitulo}>
        <Route size={14} aria-hidden /> Quem faz e onde
        {carregando ? <span className={styles.previaNota}> · atualizando…</span> : null}
      </p>

      {recusa ? (
        <div className={styles.previaRecusa} role="alert">
          <p><TriangleAlert size={13} aria-hidden /> <strong>{recusa.titulo}.</strong> {recusa.passo}</p>
          <p className={styles.previaNota}>{recusa.mensagem}</p>
        </div>
      ) : null}

      {!recusa && previa ? (
        previa.targets.length === 0 ? (
          <p className={styles.previaNota}>
            {previa.questions.length > 0 ? 'Os alvos dependem das respostas abaixo.' : 'Nenhum aparelho resolvido ainda.'}
          </p>
        ) : (
          <ul className={styles.previaLista}>
            {previa.targets.map((t) => {
              const quem = t.profile_id ? nomeDe(t.profile_id) : null;
              const origem = ORIGEM[t.origem] ?? { rotulo: t.origem, dica: '' };
              // Item 24.6: o CONJUNTO de apps do alvo, não só o primeiro — um comando entre apps toca mais de um.
              const nomesDosApps = appsDoAlvo(t, apps ?? []);
              return (
                <li key={`${t.profile_id ?? '-'}:${t.instance_id}`} className={styles.previaAlvo}>
                  {quem ? (
                    <span className={styles.previaQuem}>
                      <Avatar src={profileAvatarUrl(t.profile_id as string)} name={quem.nome} size={22} />
                      <strong>{quem.nome}</strong>
                    </span>
                  ) : <span className={styles.previaNota}>sem persona</span>}
                  <ArrowRight size={13} aria-hidden className={styles.previaSeta} />
                  <span className="mono">{t.instance_id}</span>
                  <Badge size="sm" tone={t.origem === 'texto' ? 'warning' : t.origem === 'ui' ? 'info' : 'neutral'}
                         title={origem.dica}>
                    origem: {origem.rotulo}
                  </Badge>
                  {nomesDosApps.length > 0 ? (
                    <span className={styles.previaNota} title={nomesDosApps.length > 1 ? 'Apps exigidos por este comando' : undefined}>
                      {nomesDosApps.join(' + ')}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )
      ) : null}
      {!recusa && !previa && carregando ? <p className={styles.previaNota}>Resolvendo os alvos…</p> : null}

      {textoTinhaDestinos ? (
        <div className={styles.previaSemDestinos}>
          <p>
            O texto citava destinos; a IA recebe: <q>{semDestinos}</q>
          </p>
          <Button size="sm" variant="ghost" icon={Eraser} onClick={() => onSemDestinos(semDestinos)}>
            Tirar os destinos do texto
          </Button>
        </div>
      ) : null}

      {previa?.warnings.map((w) => (
        <p key={w} className={styles.previaAviso}><TriangleAlert size={13} aria-hidden /> {comNomes(w)}</p>
      ))}

      {previa && previa.questions.length > 0 ? (
        <div className={styles.previaPerguntas} role="group" aria-label="Perguntas da prévia">
          <p className={styles.previaTitulo}><CircleHelp size={14} aria-hidden /> Responda antes de executar</p>
          {previa.questions.map((q, i) => (
            <div key={`${q.code}-${i}`} className={styles.previaPergunta}>
              <p>{q.question}</p>
              <div className={styles.previaOpcoes}>
                {q.options.map((o) => (
                  <button key={o} type="button" className={cx(ui.chip, styles.previaOpcao)}
                          onClick={() => onResponder(q, o)}>
                    {q.field === 'profile_id' ? (
                      <Avatar src={profileAvatarUrl(o)} name={rotuloDaOpcao(q, o)} size={18} />
                    ) : null}
                    {rotuloDaOpcao(q, o)}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
