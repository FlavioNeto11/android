/**
 * Vínculo persona × aparelho (N:N, ADR-043): a frase de cada recusa, com o próximo passo. A recusa D2-a (uma conta
 * por app em cada aparelho) chega com o id da outra persona só na mensagem — o 409 não traz `details` —, então o
 * nome dela vem de `GET /instances/{id}/personas`, que diz quem está lá e para que app.
 */
import { api, hintForError, type ApiError } from '../../api/client';

export interface RecusaDoVinculo {
  titulo: string;
  /** O que aconteceu e o que fazer, em português, com os nomes. */
  texto: string;
  /** A mensagem do backend, como veio. */
  mensagem: string;
}

export async function recusaDoVinculo(e: ApiError, contexto: {
  instanceId: string;
  appId: string | null;
  appNome: string | null;
  profileId: string;
}): Promise<RecusaDoVinculo> {
  const base = { mensagem: e.message };
  switch (e.code) {
    case 'conta_do_app_ja_no_aparelho': {
      let outra: string | null = null;
      try {
        const la = await api.instancePersonas(contexto.instanceId);
        const quem = la.find((p) => p.profile_id !== contexto.profileId && (contexto.appId === null || p.app_id === contexto.appId))
          ?? la.find((p) => p.profile_id !== contexto.profileId);
        outra = quem ? quem.name || quem.display_name || (quem.username ? `@${quem.username}` : quem.profile_id) : null;
      } catch {
        // Sem a lista, a frase fica sem o nome; a mensagem do backend (com o id) continua embaixo.
      }
      const app = contexto.appNome ?? 'deste app';
      return {
        ...base, titulo: 'Uma conta por app em cada aparelho',
        texto: `O aparelho ${contexto.instanceId} já tem a conta do ${app}${outra ? ` de ${outra}` : ' de outra persona'}; um `
          + 'aparelho tem uma conta por app enquanto a troca de conta for manual. Escolha outro aparelho (ou outro app), '
          + `ou desvincule ${outra ?? 'a outra persona'} de ${contexto.instanceId} antes.`,
      };
    }
    case 'persona_in_use':
      return { ...base, titulo: 'Persona em uso neste aparelho',
               texto: `Há execução desta persona em andamento em ${contexto.instanceId}. Espere terminar, ou cancele a `
                 + 'execução (tela Execuções), e desvincule depois.' };
    case 'not_bound':
      return { ...base, titulo: 'Vínculo não existe mais', texto: 'Outra tela já mudou este vínculo. Atualize a persona.' };
    case 'store_instance':
      return { ...base, titulo: 'A loja não recebe persona',
               texto: 'O aparelho-loja só guarda o aplicativo oficial e não executa tarefas. Escolha um aparelho do parque.' };
    case 'unknown_instance':
      return { ...base, titulo: 'Aparelho desconhecido', texto: 'Este aparelho não está no parque. Atualize a lista.' };
    case 'unknown_app':
      return { ...base, titulo: 'Aplicativo não cadastrado', texto: 'Escolha um aplicativo da lista, ou nenhum.' };
    default:
      return { ...base, titulo: 'Não foi possível mudar o vínculo', texto: hintForError(e) };
  }
}
