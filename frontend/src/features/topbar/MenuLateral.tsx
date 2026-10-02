import {
  CalendarClock, GraduationCap, Inbox, LayoutGrid, ListChecks, Package, PanelLeftClose, PanelLeftOpen, Server, Settings as SettingsIcon,
  Stethoscope, UserRound, X, type LucideIcon,
} from 'lucide-react';
import { useEffect, useRef } from 'react';
import { hashDe, type Tela } from '../../lib/rotas';
import { focarConteudo } from '../../lib/scroll';
import { useSessionStore } from '../../store/session';
import { PARAM_FOCO, useUiStore } from '../../store/ui';
import { useContagemDoAprendizado } from '../aprendizado/contagem';
import { elementosFocaveis } from '../focus/Drawer';
import { falaDoTotal, numeroExibido } from '../pendencias/exibicao';
import { usePendenciasStore } from '../pendencias/store';
import { usePedidosStore, useReleituraDosPedidos } from '../pedidos/store';
import { usePendencias, useReleituraDasPendencias } from '../pendencias/usePendencias';
import styles from './MenuLateral.module.css';

/**
 * Menu principal: coluna lateral recolhível (ícone + rótulo; recolhida, só ícone) a partir de 1024 px, e gaveta
 * aberta pelo botão "Menu" do topo abaixo disso. Substitui a faixa horizontal do topo, que cortava seções sem pista
 * (744 px de seções em 701 px a 1440 px; 5 de 8 somiam a 390 px). Uma lista vertical não precisa medir largura: as
 * oito telas (e as que vierem) ficam sempre alcançáveis.
 */
export const NAV: readonly { tela: Tela; label: string; icon: LucideIcon }[] = [
  { tela: 'painel', label: 'Painel', icon: LayoutGrid },
  { tela: 'personas', label: 'Personas', icon: UserRound },
  { tela: 'aplicativos', label: 'Aplicativos', icon: Package },
  { tela: 'execucoes', label: 'Execuções', icon: ListChecks },
  { tela: 'pedidos', label: 'Pedidos', icon: CalendarClock },
  { tela: 'pendencias', label: 'Pendências', icon: Inbox },
  { tela: 'aprendizado', label: 'Aprendizado', icon: GraduationCap },
  { tela: 'infraestrutura', label: 'Infraestrutura', icon: Server },
  { tela: 'configuracao', label: 'Configuração', icon: SettingsIcon },
  { tela: 'diagnostico', label: 'Diagnóstico', icon: Stethoscope },
];

export const ID_MENU = 'menu-principal';
export const ID_BOTAO_MENU = 'botao-menu';

/** A gaveta só existe abaixo de 1024 px (`MenuLateral.module.css`). Sem `matchMedia` (jsdom), vale como gaveta. */
function ehGaveta(): boolean {
  return typeof window.matchMedia !== 'function' || !window.matchMedia('(min-width: 1024px)').matches;
}

/** A contagem "Para aprovar" do Aprendizado (ADR-054, D1). A releitura é a da caixa de Pendências (uma leitura a cada
 *  minuto alimenta as duas); sem sessão não há leitura, e falha é silenciosa (o store explica por quê). */
function usePendentesDoAprendizado(): number | null {
  const operator = useSessionStore((s) => s.operator);
  const pendentes = useContagemDoAprendizado((s) => s.pendentes);
  return operator ? pendentes : null;
}

export function MenuLateral() {
  const view = useUiStore((s) => s.view);
  const foco = useUiStore((s) => s.focusInstanceId);
  const recolhido = useUiStore((s) => s.menuRecolhido);
  const aberto = useUiStore((s) => s.menuAberto);
  const setMenuRecolhido = useUiStore((s) => s.setMenuRecolhido);
  const setMenuAberto = useUiStore((s) => s.setMenuAberto);
  const paraAprovar = usePendentesDoAprendizado();
  useReleituraDasPendencias();
  useReleituraDosPedidos();
  // Avisos não lidos da caixa de avisos dos pedidos: contador próprio, que nunca se chama pendência (ADR-062).
  const avisosNaoLidos = usePedidosStore((s) => s.naoLidos);
  // O número do item Pendências é o total da lista da própria tela (mesma função), não uma soma à parte.
  const { total: pendencias, falhou: pendenciasIncompleto } = usePendencias();
  // B8: se a fila do Aprendizado não carregou, o "Para aprovar" também é um piso (a contagem guardada pode ser velha).
  const aprendizadoIncompleto = usePendenciasStore((s) => s.falhas.aprendizado);
  const navRef = useRef<HTMLElement>(null);
  const abertoAntes = useRef(false);
  const foiNavegacao = useRef(false);

  // Gaveta: ao abrir, o teclado entra no item ativo (ou no primeiro); ao fechar, volta ao botão "Menu".
  useEffect(() => {
    if (aberto) {
      const alvo = navRef.current?.querySelector<HTMLElement>('a[aria-current="page"]')
        ?? navRef.current?.querySelector<HTMLElement>('a');
      alvo?.focus();
    } else if (abertoAntes.current) {
      // Escolheu uma seção: o teclado segue para o conteúdo novo (um Tab já cai no primeiro controle da tela), e não
      // de volta ao botão "Menu". Esc ou "Fechar" não trocaram de tela: voltam ao botão que abriu a gaveta.
      if (!(foiNavegacao.current && focarConteudo())) document.getElementById(ID_BOTAO_MENU)?.focus();
      foiNavegacao.current = false;
    }
    abertoAntes.current = aberto;
  }, [aberto]);

  // Gaveta aberta = modal: o Tab fica preso nela e o Esc a fecha com o foco em QUALQUER lugar (RF-26 da revisão final:
  // o Tab saía para "Reconectar agora", atrás do fundo escurecido, e dali o Esc deixava de valer, porque o ouvinte
  // morava no `<nav>`). Ouvinte no documento enquanto estiver aberta; o foco volta ao botão "Menu" pelo efeito acima.
  useEffect(() => {
    if (!aberto) return undefined;
    const aoTeclar = (e: KeyboardEvent) => {
      const nav = navRef.current;
      if (!nav || e.defaultPrevented || !ehGaveta()) return;
      const alvo = e.target instanceof HTMLElement ? e.target : null;
      // Esc de uma caixa de diálogo é dela (aberta por cima da gaveta, ou no portal de um popover).
      if (e.key === 'Escape') {
        if (alvo?.closest('dialog, [role="dialog"]')) return;
        e.preventDefault();
        setMenuAberto(false);
        return;
      }
      if (e.key !== 'Tab') return;
      const lista = elementosFocaveis(nav);
      const primeiro = lista[0];
      const ultimo = lista[lista.length - 1];
      if (!primeiro || !ultimo) {
        e.preventDefault();
        nav.focus();
        return;
      }
      const dentro = alvo !== null && nav.contains(alvo);
      if (!dentro) {                         // o foco já escapou (clique no fundo, no topo): volta para dentro
        e.preventDefault();
        (e.shiftKey ? ultimo : primeiro).focus();
      } else if (!e.shiftKey && alvo === ultimo) {
        e.preventDefault();
        primeiro.focus();
      } else if (e.shiftKey && alvo === primeiro) {
        e.preventDefault();
        ultimo.focus();
      }
    };
    document.addEventListener('keydown', aoTeclar);
    return () => document.removeEventListener('keydown', aoTeclar);
  }, [aberto, setMenuAberto]);

  return (
    <>
      {aberto ? <div className={styles.fundo} aria-hidden onClick={() => setMenuAberto(false)} /> : null}
      <nav
        ref={navRef}
        id={ID_MENU}
        aria-label="Seções"
        className={styles.menu}
        data-recolhido={recolhido || undefined}
        data-aberto={aberto || undefined}
      >
        <div className={styles.cabecalhoGaveta}>
          <span className={styles.tituloGaveta}>Seções</span>
          <button type="button" className={styles.fechar} aria-label="Fechar menu" onClick={() => setMenuAberto(false)}>
            <X size={18} aria-hidden />
          </button>
        </div>
        <ul className={styles.lista}>
          {NAV.map(({ tela, label, icon: Icon }) => {
            const incompleto = tela === 'aprendizado' ? aprendizadoIncompleto : tela === 'pendencias' ? pendenciasIncompleto : false;
            const n = tela === 'aprendizado' ? (paraAprovar ?? (incompleto ? 0 : null)) : tela === 'pendencias' ? pendencias
              : tela === 'pedidos' ? avisosNaoLidos : null;
            // Zero só some se a leitura foi completa: com uma origem fora, "0" não é número, e o selo vira "?".
            const conta = n !== null && (n > 0 || incompleto) ? n : null;
            const legenda = tela === 'pendencias' ? 'aguardando você' : tela === 'pedidos' ? 'avisos não lidos' : 'para aprovar';
            return (
              <li key={tela}>
                {/* O `foco` vai junto: trocar de tela não fecha o aparelho aberto no painel de Foco. */}
                <a
                  href={hashDe(tela, { query: foco ? { [PARAM_FOCO]: foco } : {} })}
                  className={styles.item}
                  aria-current={view === tela ? 'page' : undefined}
                  // Nome explícito: recolhido, o rótulo só existe como texto fora da vista e o `title` não é nome confiável.
                  // O aria-label vale no lugar do conteúdo, então leva junto a contagem que o selo mostra. WCAG 2.5.3: o
                  // nome COMEÇA pelo que se vê ("Pendências 4"); o que vem depois ("aguardando você") só o explica. Por
                  // isso o selo não repete a legenda em texto escondido: o texto visível do item é só rótulo + número.
                  aria-label={conta !== null ? `${label}, ${falaDoTotal(conta, incompleto, legenda)}` : label}
                  title={recolhido ? label : undefined}
                  onClick={() => {
                    foiNavegacao.current = aberto;
                    setMenuAberto(false);
                  }}
                >
                  <Icon size={18} aria-hidden className={styles.icone} />
                  <span className={styles.rotulo}>{label}</span>
                  {/* Pendências: tudo o que espera uma decisão sua. Aprendizado: a fila do D1 (o que o sistema não publica
                      sozinho). O espaço antes do selo não pesa no layout flex, mas separa "Pendências" de "4" no texto
                      visível que o WCAG 2.5.3 compara com o nome ("Pendências4" não está em "Pendências 4, …"). */}
                  {conta !== null ? (
                    <>
                      {' '}
                      <span className={styles.contagem}
                            title={incompleto
                              ? `${numeroExibido(conta, true)} ${legenda}: alguma origem não carregou, o número pode ser maior`
                              : `${conta} ${conta === 1 ? 'item' : 'itens'} ${legenda}`}>
                        {numeroExibido(conta, incompleto)}
                      </span>
                    </>
                  ) : null}
                </a>
              </li>
            );
          })}
        </ul>
        <button
          type="button"
          className={styles.recolher}
          aria-expanded={!recolhido}
          onClick={() => setMenuRecolhido(!recolhido)}
          title={recolhido ? 'Expandir menu' : undefined}
        >
          {recolhido ? <PanelLeftOpen size={18} aria-hidden /> : <PanelLeftClose size={18} aria-hidden />}
          <span className={styles.rotulo}>{recolhido ? 'Expandir menu' : 'Recolher menu'}</span>
        </button>
      </nav>
    </>
  );
}
