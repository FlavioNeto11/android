import { useLayoutEffect, useRef, useState, type ElementType, type RefObject } from 'react';
import { cx } from '../lib/format';
import styles from './TruncatedText.module.css';

/**
 * Diz se o conteúdo do elemento está de fato cortado (uma linha com reticências, ou o limite de linhas do `line-clamp`).
 * Mede de novo quando o elemento muda de tamanho (janela, painel, fonte) e quando o texto muda. Sem `ResizeObserver`
 * (jsdom, navegador antigo) mede só no encaixe e a cada texto novo.
 */
export function useTransborda(ref: RefObject<HTMLElement | null>, texto: string, linhas: number): boolean {
  const [transborda, setTransborda] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    // 1 px de folga: `scrollWidth` arredonda para cima e acusaria corte em texto que cabe exato.
    const medir = () => {
      const cortado = linhas > 1 ? el.scrollHeight > el.clientHeight + 1 : el.scrollWidth > el.clientWidth + 1;
      setTransborda(cortado);
    };
    medir();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(medir);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, texto, linhas]);

  return transborda;
}

interface TruncatedTextProps {
  /** O texto mostrado (e, por padrão, o que aparece inteiro no tooltip quando é cortado). */
  children: string;
  /**
   * O texto integral, quando o mostrado já é uma versão curta dele (título curto de uma execução). Com `completo`
   * diferente de `children`, o `title` fica sempre presente: o que se vê nunca é tudo o que existe.
   */
  completo?: string;
  /** Linhas visíveis antes do corte. 1 (padrão) = uma linha com reticências; 2 ou mais = `line-clamp`. */
  linhas?: number;
  as?: ElementType;
  className?: string;
}

/**
 * Texto que pode ser cortado com reticências e que só ganha `title` quando realmente é. O `title` não aparece em tela
 * de toque: onde o dado é importante num celular, prefira deixar o texto quebrar linha (sem este componente) e use-o
 * onde o corte é a escolha certa (nome longo numa lista densa, título de objetivo em duas linhas).
 */
export function TruncatedText({ children, completo, linhas = 1, as: Tag = 'span', className }: TruncatedTextProps) {
  const ref = useRef<HTMLElement | null>(null);
  const transborda = useTransborda(ref, children, linhas);
  const integral = completo ?? children;
  const comTitle = transborda || integral !== children;
  return (
    <Tag
      ref={ref}
      className={cx(linhas > 1 ? styles.clamp : styles.linha, className)}
      style={linhas > 1 ? { WebkitLineClamp: linhas } : undefined}
      title={comTitle ? integral : undefined}
    >
      {children}
    </Tag>
  );
}
