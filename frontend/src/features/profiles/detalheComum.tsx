/**
 * Peças comuns às guias da persona: a versão ao vivo, a lista carregada quando a guia abre, a linha rótulo/valor e
 * o esqueleto de carga. Moravam no `ProfileDetail.tsx` de 1.400 linhas; cada guia agora vive no seu arquivo.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { isRecord } from '../../lib/format';
import { onLiveEvent } from '../../store/live';
import { toastError } from '../../store/toasts';
import { idsDosAparelhos, type Pessoa } from './pessoa';
import styles from './Profiles.module.css';

/** Sobe quando chega evento persistido DESTE perfil ou de um dos aparelhos dele — no máximo uma vez a cada 1,5 s.
 *  As abas põem a versão nas dependências e recarregam sozinhas enquanto o perfil age (antes carregavam só ao
 *  abrir). Com N aparelhos (v0.29), qualquer um deles conta: a persona age em todos. */
export function useVersaoAoVivo(profile: Pick<Pessoa, 'id' | 'instance_id' | 'policy_group_id' | 'session' | 'devices'>): number {
  const [versao, setVersao] = useState(0);
  const aparelhos = idsDosAparelhos(profile).join(',');
  const grupo = profile.policy_group_id ?? null;
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const desligar = onLiveEvent((ev) => {
      if (ev.id === null) return;                       // quadro/métrica efêmera: não muda dado de perfil
      const doPerfil = isRecord(ev.data) && (ev.data.profile_id === profile.id
        || (!!grupo && ev.data.group_id === grupo));        // editar o grupo muda o que vale para este perfil
      if (!doPerfil && !(ev.instance_id && aparelhos.split(',').includes(ev.instance_id))) return;
      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        setVersao((v) => v + 1);
      }, 1500);
    });
    return () => {
      desligar();
      if (timer) clearTimeout(timer);
    };
  }, [profile.id, aparelhos, grupo]);
  return versao;
}

/** Carrega uma lista quando a aba abre. Erro vira estado vazio com aviso — nunca tela quebrada. */
export function useLista<T>(carregar: () => Promise<T[]>, deps: unknown[]): [T[] | null, () => Promise<void>] {
  const [dados, setDados] = useState<T[] | null>(null);
  const token = useRef(0);
  const recarregar = useCallback(async () => {
    const meu = ++token.current;
    try {
      const r = await carregar();
      if (meu === token.current) setDados(r);
    } catch (e) {
      if (meu === token.current) setDados([]);
      toastError('Não foi possível carregar', e);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    void recarregar();
  }, [recarregar]);
  return [dados, recarregar];
}

export function Linha({ rotulo, children }: { rotulo: string; children: React.ReactNode }) {
  return (
    <div className={styles.row}>
      <dt>{rotulo}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function Carregando({ children }: { children?: React.ReactNode }) {
  return (
    <LoadingRegion label="Carregando…">
      <Skeleton height={80} />
      {children}
    </LoadingRegion>
  );
}
