import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { PersonaDTO } from '../../api/types';
import { useAppStore } from '../../store/app';

/**
 * A leitura em andamento é UMA só para quem a pede junto (a barra, a tela e o Comando montam no mesmo instante): cada
 * montagem chamava `GET /personas` por conta própria e a rede via a mesma lista de 3 a 5 vezes a cada troca de tela.
 * Só divide a leitura que ainda não voltou; depois de voltar, a próxima montagem lê de novo (a lista pode ter mudado).
 */
let emVoo: Promise<PersonaDTO[]> | null = null;
function lerPersonas(): Promise<PersonaDTO[]> {
  emVoo ??= api.listPersonas().finally(() => { emVoo = null; });
  return emVoo;
}

/**
 * As personas (todas: com e sem conta), relidas a cada snapshot novo. Personas não vêm no snapshot nem em eventos —
 * são poucas e mudam devagar —, o mesmo padrão que a grade e a Infraestrutura já usavam com os perfis. `null`
 * enquanto a primeira leitura não chega; falha vira lista vazia (quem usa mostra só o que sabe, nunca uma tela
 * quebrada). `ativo = false` não lê nada (ex.: o Comando fora do modo "Por persona").
 */
export function usePersonas(ativo = true, chaveExtra: unknown = null): PersonaDTO[] | null {
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  const [pessoas, setPessoas] = useState<PersonaDTO[] | null>(null);
  useEffect(() => {
    if (!ativo) return;
    let vivo = true;
    lerPersonas()
      .then((p) => { if (vivo) setPessoas(p); })
      .catch(() => { if (vivo) setPessoas((atual) => atual ?? []); });
    return () => {
      vivo = false;
    };
  }, [ativo, hydrateCount, chaveExtra]);
  return pessoas;
}
