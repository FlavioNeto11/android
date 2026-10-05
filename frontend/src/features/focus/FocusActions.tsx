/**
 * A seção "Ações" do Foco, montada a partir de `focusActionGroups` (regra pura, testada em node): ciclo de vida,
 * apps, controle manual, observação, indisponíveis e, por último e separada, a zona de perigo.
 *
 * O controle manual só aparece com o controle na mão. Antes, sem ele, o painel mostrava onze botões e um campo
 * de texto desabilitados, cada um repetindo "Assuma o controle": agora o motivo aparece uma vez.
 */
import {
  CircleSlash, CornerDownLeft, Delete, HelpCircle, ListTree, PackageCheck, RefreshCw, ScanSearch, Send, Store,
  TriangleAlert, type LucideIcon,
} from 'lucide-react';
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import type { Command, Instance, InstanceAction } from '../../api/types';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Disclosure } from '../../components/Disclosure';
import { Checkbox, TextInput } from '../../components/Field';
import { Tooltip } from '../../components/Tooltip';
import { ACTION_META, cancelarComando, runInstanceAction } from '../devices/actions';
import { MOTIVO_SERVIDOR_SEM_RESPOSTA, type FocusActionGroups, type FocusItem, type FocusVerb } from '../devices/deviceState';
import { InstallAppMenu } from '../devices/InstallAppMenu';
import { OpenAppMenu } from '../devices/OpenAppMenu';
import styles from './Focus.module.css';
import { FocusSection } from './FocusSection';
import { verificarApp } from './FocusInfoSections';

export type ManualKey = 'back' | 'home' | 'recents' | 'enter' | 'delete';

const ICONE: Partial<Record<FocusVerb, LucideIcon>> = {
  enter: CornerDownLeft,
  delete: Delete,
  verify_app: PackageCheck,
  refresh_frame: RefreshCw,
  reload_context: ScanSearch,
  hierarchy: ListTree,
  cancel_command: CircleSlash,
};

function iconeDe(action: FocusVerb): LucideIcon | undefined {
  return ICONE[action] ?? ACTION_META[action as InstanceAction]?.icon;
}

function Grupo({ title, className, children }: { title: string; className?: string; children: ReactNode }) {
  return (
    <div className={className ?? styles.actionGroup} role="group" aria-label={title}>
      <h4 className={styles.actionGroupTitle}>{title}</h4>
      {children}
    </div>
  );
}

export function FocusActions({
  instance, groups, openCmd, busyAction, mine, pending, sending, gravando = false, defaultApp, desconhecido = false, contextLoading,
  onKey, onText, onRefreshFrame, onReloadContext, onShowHierarchy,
}: {
  instance: Instance;
  groups: FocusActionGroups;
  openCmd: Command | undefined;
  busyAction: InstanceAction | undefined;
  mine: boolean;
  pending: boolean;
  sending: boolean;
  /** Há gravação do Modo treinamento em andamento: "Limpar o campo antes" já vem marcada (31.84). */
  gravando?: boolean;
  /** O app padrão do aparelho: é o que "Verificar app" relê (a lista em Apps verifica cada um). */
  defaultApp: { name: string; package: string } | null;
  /** Servidor do aparelho fora do ar ou sem canal (RF-40): o motivo de controle manual não sai do estado guardado. */
  desconhecido?: boolean;
  contextLoading: boolean;
  onKey: (key: ManualKey) => void;
  onText: (text: string, clearFirst: boolean) => Promise<boolean>;
  onRefreshFrame: () => void;
  onReloadContext: () => void;
  onShowHierarchy: () => void;
}) {
  const [text, setText] = useState('');
  // null = a pessoa não mexeu: vale o padrão (marcada só enquanto grava). Depois de mexer, vale a escolha dela.
  const [limparEscolha, setLimparEscolha] = useState<boolean | null>(null);
  const limpar = limparEscolha ?? gravando;
  // A escolha vale para esta gravação: ao começar ou terminar uma, volta ao padrão.
  useEffect(() => setLimparEscolha(null), [gravando]);
  const { id } = instance;
  const loja = instance.kind === 'store';

  const executar = async (action: InstanceAction) => {
    // Parar no meio de uma execução tira o aparelho da etapa em andamento: o clique pede confirmação. Sem
    // execução, Parar continua de um clique, como no cartão.
    if (action === 'stop' && instance.current?.run_id) {
      const { confirmed } = await confirm({
        title: `Parar ${id} no meio de uma execução?`,
        danger: true,
        confirmLabel: 'Parar o aparelho',
        cancelLabel: 'Deixar rodando',
        body: (
          <p>
            A execução <span className="mono">{instance.current.run_id}</span> está usando este aparelho
            {instance.current.step_title ? ` (etapa “${instance.current.step_title}”)` : ''}. Parar o emulador
            interrompe a etapa em andamento; o que já foi feito no aparelho não é desfeito.
          </p>
        ),
      });
      if (!confirmed) return;
    }
    await runInstanceAction(id, action);
  };

  const botao = (item: FocusItem, variant?: 'primary' | 'outline') => {
    const action = item.action as InstanceAction;
    return (
      <Button
        key={item.action}
        variant={variant}
        icon={iconeDe(item.action)}
        loading={busyAction === action || openCmd?.verb === action}
        disabled={!!busyAction && busyAction !== action}
        disabledReason={item.disabledReason}
        onClick={() => void executar(action)}
      >
        {item.label}
      </Button>
    );
  };

  const submeter = async (e: FormEvent) => {
    e.preventDefault();
    if (!text) return;
    if (await onText(text, limpar)) setText('');
  };

  const travaManual = groups.manual[0]?.disabledReason ?? null;
  const motivoSemControle =
    desconhecido ? MOTIVO_SERVIDOR_SEM_RESPOSTA
    : instance.state === 'hibernated' ? 'O aparelho está hibernado: acorde-o para interagir.'
    : instance.state !== 'online' ? 'O aparelho precisa estar online para o controle manual.'
    : pending ? 'Pedido de controle enviado: aguardando a IA concluir a ação atual.'
    : 'Assuma o controle (na faixa do topo) para tocar na tela, digitar e usar as teclas do Android.';

  const observar: Record<string, () => void> = {
    refresh_frame: onRefreshFrame, reload_context: onReloadContext, hierarchy: onShowHierarchy,
  };

  return (
    <FocusSection title="Ações">
      <div className={styles.actions}>
        {groups.primary || groups.lifecycle.length ? (
          <Grupo title="Ciclo de vida">
            <div className={styles.quick}>
              {groups.primary ? botao(groups.primary, instance.state === 'error' ? 'outline' : 'primary') : null}
              {groups.lifecycle.map((item) => botao(item))}
            </div>
          </Grupo>
        ) : null}

        <Grupo title="Apps">
          <div className={styles.quick}>
            {groups.apps.map((item) => item.action === 'open_app' ? (
              <OpenAppMenu key={item.action} padrao={instance.app_id} disabledReason={item.disabledReason}
                           loading={busyAction === 'open_app' || openCmd?.verb === 'open_app'}
                           disabled={!!busyAction && busyAction !== 'open_app'}
                           onPick={(appId) => void runInstanceAction(id, 'open_app', { app_id: appId })} />
            ) : item.action === 'install_apk' ? (
              // Instalar escolhe o SEU app, independente de "Abrir app": nada é deduzido do controle vizinho.
              <InstallAppMenu key={item.action} padrao={instance.app_id} disabledReason={item.disabledReason}
                              loading={busyAction === 'install_apk' || openCmd?.verb === 'install_apk'}
                              disabled={!!busyAction && busyAction !== 'install_apk'}
                              onPick={(appId) => void runInstanceAction(id, 'install_apk', { app_id: appId })} />
            ) : (
              <Button key={item.action} icon={iconeDe(item.action)}
                      disabledReason={item.disabledReason
                        ?? (defaultApp ? null : 'Nenhum app associado a este aparelho: verifique pela lista da seção Apps.')}
                      onClick={() => defaultApp && void verificarApp(id, defaultApp.package, defaultApp.name)}>
                {item.label}
              </Button>
            ))}
          </div>
        </Grupo>

        <Grupo title="Controle manual">
          {mine ? (
            <>
              <div className={styles.manualHead}>
                {travaManual ? <p className={styles.groupHint}>{travaManual}</p> : <span />}
                <Tooltip content={<>Na tela: <b>clique</b> = toque · <b>segurar ≥ 0,6 s</b> = toque longo ·{' '}
                  <b>arrastar</b> = deslizar (a duração acompanha o gesto).</>}>
                  <button type="button" className={styles.hintBtn} aria-label="Como tocar na tela">
                    <HelpCircle size={14} aria-hidden /> Gestos
                  </button>
                </Tooltip>
              </div>
              {/* 31.85: um clique durante o envio é ignorado; a faixa diz por que, para a pessoa esperar. A região
                  existe sempre para o leitor de tela anunciar quando o texto entra. */}
              <p className={styles.groupHint} role="status" aria-live="polite">{sending ? 'Enviando ao aparelho…' : ''}</p>
              <div className={styles.keys} role="group" aria-label="Botões do Android">
                {groups.manual.map((item) => (
                  <Button key={item.action} icon={iconeDe(item.action)} disabled={sending}
                          disabledReason={item.disabledReason} onClick={() => onKey(item.action as ManualKey)}>
                    {item.label}
                  </Button>
                ))}
              </div>
              {/* Decisão 4 do plano (dono, 24/09): a loja aceita texto pelo painel como os outros aparelhos. Só a
                  SENHA da conta Google não passa — o backend recusa com `store_password_blocked`. */}
              {loja ? (
                <p className={styles.groupHint}>
                  <Store size={12} aria-hidden /> Na loja, a senha da conta Google é digitada direto na janela do
                  emulador; os demais textos (busca, nomes) vão por aqui.
                </p>
              ) : null}
              <form className={styles.textRow} onSubmit={(e) => void submeter(e)}>
                <TextInput
                  value={text}
                  placeholder="Texto para digitar no aparelho"
                  aria-label="Texto para digitar no aparelho"
                  disabled={!!travaManual}
                  onChange={(e) => setText(e.target.value)}
                />
                <Button type="submit" icon={Send} loading={sending && !!text}
                        disabledReason={travaManual ?? (text ? null : 'Digite um texto para enviar.')}>
                  Enviar
                </Button>
              </form>
              <Checkbox label="Limpar o campo antes" checked={limpar} disabled={!!travaManual}
                        onChange={(e) => setLimparEscolha(e.target.checked)} />
            </>
          ) : (
            <p className={styles.groupHint}>{motivoSemControle}</p>
          )}
        </Grupo>

        <Grupo title="Observação">
          <div className={styles.quick}>
            {groups.observe.map((item) => (
              <Button key={item.action} variant="ghost" icon={iconeDe(item.action)} disabledReason={item.disabledReason}
                      loading={item.action === 'reload_context' && contextLoading}
                      onClick={() => observar[item.action]?.()}>
                {item.label}
              </Button>
            ))}
          </div>
        </Grupo>

        {groups.unavailable.length ? (
          // Filhos montados de saída (não uma função): o motivo de cada verbo também serve à busca da página.
          <Disclosure summary={`Indisponíveis (${groups.unavailable.length})`} bare>
            <ul className={styles.unavailableList}>
              {groups.unavailable.map((u) => (
                <li key={u.action}><strong>{u.label}</strong> — {u.reason}</li>
              ))}
            </ul>
          </Disclosure>
        ) : null}

        {groups.danger.length ? (
          <Grupo title="Zona de perigo" className={styles.dangerZone}>
            <p className={styles.dangerHint}><TriangleAlert size={13} aria-hidden /> Estas ações não se desfazem.</p>
            <div className={styles.quick}>
              {groups.danger.map((item) => item.action === 'cancel_command' ? (
                <Button key={item.action} variant="dangerGhost" icon={iconeDe(item.action)}
                        onClick={() => openCmd && void cancelarComando(openCmd)}>
                  {item.label}
                </Button>
              ) : (
                <Button key={item.action} variant="dangerGhost" icon={iconeDe(item.action)}
                        loading={busyAction === 'reset' || openCmd?.verb === 'reset'}
                        disabled={!!busyAction && busyAction !== 'reset'}
                        disabledReason={item.disabledReason}
                        // `runInstanceAction` pede a confirmação do reset (apaga tudo do emulador) e manda `confirm`.
                        onClick={() => void runInstanceAction(id, 'reset')}>
                  {item.label}
                </Button>
              ))}
            </div>
          </Grupo>
        ) : null}
      </div>
    </FocusSection>
  );
}
