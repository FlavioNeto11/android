import { ChevronLeft, ChevronRight, Ellipsis, Smartphone, TriangleAlert, X } from 'lucide-react';
import { useState } from 'react';
import type { Instance, InstanceAction } from '../../api/types';
import { Button } from '../../components/Button';
import { Popover } from '../../components/Popover';
import ui from '../../components/ui.module.css';
import { cx, plural } from '../../lib/format';
import { useAppStore } from '../../store/app';
import { useUiStore } from '../../store/ui';
import { ACTION_META, runBulkAction, useBusyStore } from '../devices/actions';
import { QUICK_VERBS, bulkActionsFor, bulkBlockersFor, type BulkContext } from '../devices/deviceState';
import { installItemLabel } from '../devices/InstallAppMenu';
import devices from '../devices/Devices.module.css';
import styles from './BarraDeSelecao.module.css';

/**
 * As ações que a pessoa repete o tempo todo ficam à vista; o resto (hibernar, instalar, abrir app) e o perigoso
 * (resetar dados) vão para o menu "Mais ações". `create`/`wake` só aparecem quando a seleção precisa deles, e nesse
 * caso são justamente o que ela veio fazer.
 */
const FREQUENTES: readonly InstanceAction[] = ['create', 'wake', 'start', 'stop', 'restart'];

/** `selected` aqui exige o `id`: a barra precisa DIZER quem impede o verbo, não só escondê-lo (achado #62). */
export interface BarraDeSelecaoProps extends Omit<BulkContext, 'selected'> {
  ids: string[];
  /** Quantos aparelhos de tarefa estão marcados (a fonte única é `store/metricas`; a loja nunca entra). */
  selecionados: number;
  selected: readonly Pick<Instance, 'id' | 'supported_verbs'>[];
  /**
   * Marcados que o filtro da tela esconde (RF-01). Ficam FORA de `ids`: a barra só age no que se vê, e diz quantos
   * ficaram de fora para a pessoa não achar que a ação os alcança.
   */
  foraDoFiltro?: number;
  /**
   * Marcados à vista cujo servidor não responde (RF-40): o estado deles é desconhecido. Também ficam FORA de `ids`,
   * e a barra diz quantos foram ignorados.
   */
  desconhecidos?: number;
  /**
   * Um aparelho está aberto no drawer de Foco, que já tem estas ações: a barra mantém o lugar (a grade não pula) e
   * o contador, mas esconde os botões, para nenhuma ação aparecer duas vezes.
   */
  emFoco: boolean;
}

function motivoDoBloqueio(quem: string[], action: InstanceAction): string {
  return `${quem.length === 1 ? quem[0] : quem.join(', ')} não ${quem.length === 1 ? 'aceita' : 'aceitam'} `
    + `“${ACTION_META[action].label}”. Tire ${quem.length === 1 ? 'esse aparelho' : 'esses aparelhos'} da seleção para usar o verbo.`;
}

/**
 * Barra de seleção: fica presa ao topo da grade (nunca sobre os cartões) e só existe com aparelhos marcados. Quando não
 * cabe numa linha ela quebra (até 900 px, com o rótulo em cima e as ações embaixo), para nenhuma ação ficar fora da
 * tela (M2, rodada 2).
 */
export function BarraDeSelecao({
  ids, selecionados, selected, hasAbsent, hasHibernated, hibernation, emFoco, foraDoFiltro = 0, desconhecidos = 0,
}: BarraDeSelecaoProps) {
  const bulkBusy = useBusyStore((s) => s.bulkBusy);
  const clearSelection = useUiStore((s) => s.clearSelection);
  const todas = bulkActionsFor({ hasAbsent, hasHibernated, hibernation, selected });
  const frequentes = todas.filter((a) => FREQUENTES.includes(a));
  const noMenu = todas.filter((a) => !FREQUENTES.includes(a));
  // O verbo que sumiu da barra era um mistério: reaparece desabilitado, com quem o impede. Só os verbos que
  // o foco também oferece — `create`/`wake` dependem do estado da seleção, não de capacidade.
  const bloqueados = QUICK_VERBS
    .filter((q) => !q.needsHibernation || hibernation)
    .map((q) => ({ action: q.action, quem: bulkBlockersFor(selected, q.action) }))
    .filter((b) => b.quem.length > 0);
  const bloqueadosNaBarra = bloqueados.filter((b) => FREQUENTES.includes(b.action));
  const bloqueadosNoMenu = bloqueados.filter((b) => !FREQUENTES.includes(b.action));
  const acoesDoMenu: readonly InstanceAction[] = [...noMenu, ...bloqueadosNoMenu.map((b) => b.action), 'reset'];
  // Tudo o que está marcado está escondido pelo filtro ou sem servidor: não há sobre o que agir.
  const semAlvoVisivel = ids.length === 0;
  const comAcoes = !emFoco && !semAlvoVisivel;

  return (
    <div className={cx(styles.dock, emFoco && styles.dockEmFoco)} data-barra-de-selecao>
      <div
        className={styles.bar}
        role={comAcoes ? 'toolbar' : undefined}
        aria-label={comAcoes ? `Ação em ${plural(ids.length, 'aparelho', 'aparelhos')}` : undefined}
      >
        <span className={styles.label} aria-live="polite">
          <Smartphone size={15} aria-hidden />
          {plural(selecionados, 'selecionado', 'selecionados')}
          {foraDoFiltro > 0 ? ` (${foraDoFiltro} fora do filtro atual)` : ''}
          {desconhecidos > 0
            ? ` (${plural(desconhecidos, 'ignorado', 'ignorados')}: servidor sem resposta)`
            : ''}
        </span>
        {emFoco ? (
          <span className={styles.nota}>As ações deste aparelho estão no painel de foco.</span>
        ) : semAlvoVisivel ? (
          <>
            <span className={styles.nota}>
              {desconhecidos > 0
                ? 'Os aparelhos marcados à vista estão com o estado desconhecido: o servidor deles não está respondendo. '
                  + 'Nenhuma ação os alcança até ele voltar.'
                : 'Nenhum aparelho marcado aparece neste filtro. As ações valem só para o que está à vista.'}
            </span>
            <Button size="sm" variant="ghost" icon={X} iconOnly label="Limpar seleção" onClick={clearSelection} />
          </>
        ) : (
          <>
            {frequentes.map((a) => (
              <Button
                key={a}
                size="sm"
                icon={ACTION_META[a].icon}
                loading={bulkBusy === a}
                disabled={bulkBusy !== null && bulkBusy !== a}
                onClick={() => void runBulkAction(ids, a)}
              >
                {ACTION_META[a].label}
              </Button>
            ))}
            {bloqueadosNaBarra.map(({ action, quem }) => (
              <Button key={`x-${action}`} size="sm" icon={ACTION_META[action].icon}
                      disabledReason={motivoDoBloqueio(quem, action)}>
                {ACTION_META[action].label}
              </Button>
            ))}
            {bulkBusy !== null ? (
              <Button size="sm" icon={Ellipsis} loading={acoesDoMenu.includes(bulkBusy)}
                      disabled={!acoesDoMenu.includes(bulkBusy)}>
                Mais ações
              </Button>
            ) : (
              <Popover label="Mais ações em lote" title="Mais ações" align="end"
                       triggerClassName={cx(ui.btn, ui.btnSm)}
                       trigger={<><Ellipsis size={13} aria-hidden /> Mais ações</>}>
                {(fechar) => (
                  <MenuMaisAcoes ids={ids} noMenu={noMenu} bloqueados={bloqueadosNoMenu} fechar={fechar} />
                )}
              </Popover>
            )}
            <Button size="sm" variant="ghost" icon={X} iconOnly label="Limpar seleção" onClick={clearSelection} />
          </>
        )}
      </div>
    </div>
  );
}

const IconeReset = ACTION_META.reset.icon;

type Tela = 'raiz' | 'instalar' | 'abrir';

/**
 * O conteúdo do menu. Instalar e abrir precisam de uma segunda escolha (qual app): ela troca o conteúdo do MESMO
 * painel. Um popover dentro do outro não serviria: o clique no de dentro conta como "fora" do de fora e o fecha
 * antes de a escolha chegar.
 */
function MenuMaisAcoes({ ids, noMenu, bloqueados, fechar }: {
  ids: string[];
  noMenu: InstanceAction[];
  bloqueados: { action: InstanceAction; quem: string[] }[];
  fechar: () => void;
}) {
  const [tela, setTela] = useState<Tela>('raiz');
  const apps = useAppStore((s) => s.apps);
  const ordenados = [...apps].sort((a, b) => a.name.localeCompare(b.name));
  const rodar = (a: InstanceAction, params?: { app_id: string }) => {
    fechar();
    void runBulkAction(ids, a, params);
  };

  if (tela !== 'raiz') {
    const instalar = tela === 'instalar';
    return (
      <div className={styles.menu}>
        <button type="button" className={styles.voltar} onClick={() => setTela('raiz')}>
          <ChevronLeft size={14} aria-hidden /> Voltar
        </button>
        <p className={styles.menuTitulo}>{instalar ? 'Instalar qual aplicativo?' : 'Abrir qual aplicativo?'}</p>
        {instalar ? (
          <p className={devices.appMenuNote}>
            Instala a versão promovida pelo catálogo do painel, via ADB — não usa a Play Store do aparelho e não abre
            o app. Só vira “instalado” depois de o aparelho confirmar a versão.
          </p>
        ) : null}
        {ordenados.length === 0 ? <p className={devices.appMenuNote}>Nenhum aplicativo cadastrado.</p> : (
          <ul className={devices.appMenu}>
            {ordenados.map((a) => (
              <li key={a.id}>
                <button type="button" className={devices.appMenuItem}
                        disabled={instalar && !a.promoted_version_name}
                        title={instalar
                          ? (a.promoted_version_name ? `${a.package} · versão ${a.promoted_version_name}`
                            : 'Importe o APK em Aplicativos, prove num aparelho e promova-o.')
                          : undefined}
                        onClick={() => rodar(instalar ? 'install_apk' : 'open_app', { app_id: a.id })}>
                  <span>{instalar ? installItemLabel(a) : a.name}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <div className={styles.menu}>
      <ul className={devices.appMenu}>
        {noMenu.map((a) => {
          const Icon = ACTION_META[a].icon;
          const escolheApp = a === 'install_apk' || a === 'open_app';
          return (
            <li key={a}>
              <button type="button" className={devices.appMenuItem}
                      onClick={() => (escolheApp ? setTela(a === 'install_apk' ? 'instalar' : 'abrir') : rodar(a))}>
                <span className={styles.item}><Icon size={14} aria-hidden /> {ACTION_META[a].label}{escolheApp ? '…' : ''}</span>
                {escolheApp ? <ChevronRight size={14} aria-hidden /> : null}
              </button>
            </li>
          );
        })}
        {bloqueados.map(({ action, quem }) => {
          const Icon = ACTION_META[action].icon;
          return (
            <li key={`x-${action}`}>
              <button type="button" className={devices.appMenuItem} disabled aria-disabled="true"
                      title={motivoDoBloqueio(quem, action)}>
                <span className={styles.item}><Icon size={14} aria-hidden /> {ACTION_META[action].label}</span>
              </button>
              <p className={styles.motivo}>{motivoDoBloqueio(quem, action)}</p>
            </li>
          );
        })}
      </ul>
      {/* Zona de perigo: separada do que se faz de rotina, e a confirmação (com a lista dos aparelhos) continua. */}
      <div className={styles.perigo} role="group" aria-label="Zona de perigo">
        <p className={styles.perigoTitulo}><TriangleAlert size={12} aria-hidden /> Zona de perigo</p>
        <button type="button" className={cx(devices.appMenuItem, styles.perigoItem)} onClick={() => rodar('reset')}>
          <span className={styles.item}><IconeReset size={14} aria-hidden /> Resetar dados…</span>
        </button>
      </div>
    </div>
  );
}
