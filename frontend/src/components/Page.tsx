import type { CSSProperties, HTMLAttributes, ReactNode } from 'react';
import appStyles from '../App.module.css';
import { cx } from '../lib/format';
import { Card, CardHeader } from './Card';
import styles from './Page.module.css';

/**
 * Contrato de página (evolução 2, design §9.1). Toda tela de primeiro nível é um `Page`: largura total do `main`
 * (sem variante estreita — Configuração era a única encaixotada em 1280 px e sobrava 30 % da tela vazia) e um
 * CONTÊINER (`container-name: page`). As faixas de layout são `@container page`, não `@media`: o painel de Foco
 * encolhe o `main`, então a largura da janela não é a medida certa. Faixas em `styles/tokens.css`.
 */
interface PageProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  /** Com título, a página já abre com o `PageHeader`. */
  title?: ReactNode;
  lead?: ReactNode;
  actions?: ReactNode;
}

export function Page({ title, lead, actions, className, children, ...rest }: PageProps) {
  return (
    <div className={cx(appStyles.page, className)} {...rest}>
      {title ? <PageHeader title={title} lead={lead} actions={actions} /> : null}
      {children}
    </div>
  );
}

interface PageHeaderProps {
  title: ReactNode;
  lead?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ title, lead, actions }: PageHeaderProps) {
  return (
    <div className={appStyles.pageHeader}>
      <div>
        <h1 className={appStyles.pageTitle}>{title}</h1>
        {lead ? <p className={appStyles.pageLead}>{lead}</p> : null}
      </div>
      {actions ? <div className={styles.headerActions}>{actions}</div> : null}
    </div>
  );
}

interface PageSectionProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  /** Rodapé do cartão (ex.: a barra "Salvar" de Limites), preso à borda de baixo, não flutuando sobre a página. */
  footer?: ReactNode;
  bodyClassName?: string;
  level?: 2 | 3;
  titleId?: string;
}

/** Uma seção da página: Card com CardHeader. O corpo é um contêiner (`section`) para as grades internas. */
export function PageSection({ title, subtitle, actions, footer, bodyClassName, level = 2, titleId, className, children, ...rest }: PageSectionProps) {
  return (
    <Card className={cx(styles.section, className)} aria-labelledby={titleId} {...rest}>
      <CardHeader title={title} subtitle={subtitle} actions={actions} level={level} titleId={titleId} />
      <div className={cx(styles.sectionBody, bodyClassName)}>{children}</div>
      {footer ? <div className={styles.sectionFooter}>{footer}</div> : null}
    </Card>
  );
}

interface TableWrapProps {
  /** Nome acessível da região rolável (leitor de tela e teclado: a região recebe foco para rolar pelas setas). */
  label: string;
  className?: string;
  children: ReactNode;
}

/**
 * Tabela larga dentro da seção: rola na horizontal em vez de estourar o cartão, e o cabeçalho (`th`) fica preso
 * no topo. A região tem altura máxima (`--table-max-h`, 70 vh) — sem ela o `sticky` não teria onde agir, porque
 * `overflow-x: auto` já faz desta caixa o rolador dos dois eixos.
 */
export function TableWrap({ label, className, children }: TableWrapProps) {
  return (
    <div role="region" aria-label={label} tabIndex={0} className={cx(styles.tableWrap, className)}>
      {children}
    </div>
  );
}

interface AutoGridProps extends HTMLAttributes<HTMLDivElement> {
  /** Largura mínima de uma coluna. Nunca maior que o contêiner: `min(100%, …)` desce a UMA coluna. */
  min: string;
}

/** Grade padrão: `repeat(auto-fill, minmax(min(100%, var(--col-min)), 1fr))`. */
export function AutoGrid({ min, className, style, children, ...rest }: AutoGridProps) {
  return (
    <div className={cx(styles.autoGrid, className)} style={{ ...style, '--col-min': min } as CSSProperties} {...rest}>
      {children}
    </div>
  );
}
