import { useCallback, useEffect, useRef, useState } from 'react';
import { toLoadError, type LoadError } from '../../lib/loadError';

/** Carrega com cancelamento por geração: a resposta velha nunca sobrescreve a nova. */
export function useCarga<T>(buscar: (signal: AbortSignal) => Promise<T>, chave: string) {
  const [dado, setDado] = useState<T | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregando, setCarregando] = useState(true);
  const vez = useRef(0);
  const buscarRef = useRef(buscar);
  buscarRef.current = buscar;
  const carregar = useCallback(async () => {
    const minha = ++vez.current;
    setCarregando(true);
    try {
      const res = await buscarRef.current(new AbortController().signal);
      if (minha !== vez.current) return;
      setDado(res);
      setErro(null);
    } catch (e) {
      if (minha === vez.current) setErro(toLoadError(e));
    } finally {
      if (minha === vez.current) setCarregando(false);
    }
  }, []);
  useEffect(() => {
    setDado(null);
    void carregar();
  }, [carregar, chave]);
  return { dado, erro, carregando, carregar };
}
