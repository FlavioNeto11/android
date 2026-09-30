import {
  GraduationCap, LayoutGrid, ListChecks, Package, PanelLeftClose, PanelLeftOpen, Server, Settings as SettingsIcon,
  Stethoscope, UserRound, X, type LucideIcon,
} from 'lucide-react';
import { useEffect, useRef } from 'react';
import { formatInt } from '../../lib/format';
import { intervaloVisivel } from '../../lib/polling';
import { hashDe, type Tela } from '../../lib/rotas';
import { useSessionStore } from '../../store/session';
import { PARAM_FOCO, useUiStore } from '../../store/ui';
import { useContagemDoAprendizado } from '../aprendizado/contagem';
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
  { tela: 'aprendizado', label: 'Aprendizado', icon: GraduationCap },
  { tela: 'infraestrutura', label: 'Infraestrutura', icon: Server },
  { tela: 'configuracao', label: 'Configuração', icon: SettingsIcon },
  { tela: 'diagnostico', label: 'Diagnóstico', icon: Stethoscope },
];

export const ID_MENU = 'menu-principal';
export const ID_BOTAO_MENU = 'botao-menu';
/** O `<main>` do App (`App.tsx`), que recebe o foco quando a gaveta fecha por troca de seção. */
const ID_CONTEUDO = 'conteudo';

/** Releitura da contagem "Para aprovar" (ADR-054, D1). Só depois do login — sem sessão, a leitura só colheria 401 —
 *  e só com a aba visível. Falha é silenciosa (o store explica por quê). */
const CONTAGEM_A_CADA_MS = 60_000;

function usePendentesDoAprendizado(): number | null {
  const operator = useSessionStore((s) => s.operator);
  const pendentes = useContagemDoAprendizado((s) => s.pendentes);
  useEffect(() => {
    if (!operator) return undefined;
    const atualizar = () => void useContagemDoAprendizado.getState().atualizar();
    atualizar();
    return intervaloVisivel(atualizar, CONTAGEM_A_CADA_MS);
  }, [operator]);
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
      const destino = foiNavegacao.current ? document.getElementById(ID_CONTEUDO) : null;
      (destino ?? document.getElementById(ID_BOTAO_MENU))?.focus();
      foiNavegacao.current = false;
    }
    abertoAntes.current = aberto;
  }, [aberto]);

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
        onKeyDown={(e) => {
          if (e.key === 'Escape' && aberto) {
            e.preventDefault();
            setMenuAberto(false);
          }
        }}
      >
        <div className={styles.cabecalhoGaveta}>
          <span className={styles.tituloGaveta}>Seções</span>
          <button type="button" className={styles.fechar} aria-label="Fechar menu" onClick={() => setMenuAberto(false)}>
            <X size={18} aria-hidden />
          </button>
        </div>
        <ul className={styles.lista}>
          {NAV.map(({ tela, label, icon: Icon }) => {
            const conta = tela === 'aprendizado' && paraAprovar !== null && paraAprovar > 0 ? paraAprovar : null;
            return (
              <li key={tela}>
                {/* O `foco` vai junto: trocar de tela não fecha o aparelho aberto no painel de Foco. */}
                <a
                  href={hashDe(tela, { query: foco ? { [PARAM_FOCO]: foco } : {} })}
                  className={styles.item}
                  aria-current={view === tela ? 'page' : undefined}
                  title={recolhido ? label : undefined}
                  onClick={() => {
                    foiNavegacao.current = aberto;
                    setMenuAberto(false);
                  }}
                >
                  <Icon size={18} aria-hidden className={styles.icone} />
                  <span className={styles.rotulo}>{label}</span>
                  {/* A fila do D1: o que o sistema não publica sozinho e espera o dono. */}
                  {conta !== null ? (
                    <span className={styles.contagem} title={`${conta} item(ns) para aprovar`}>
                      <span aria-hidden>{formatInt(conta)}</span>
                      <span className="sr-only"> ({formatInt(conta)} para aprovar)</span>
                    </span>
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
