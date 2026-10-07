/**
 * Terceiros que o texto do BACKEND cita sem querer (31.254): o motivo de uma falha vem da leitura da tela e pode dizer "a tela mostra o
 * feed com o post de astro_jessica em foco (o de nasawebb …)": quem é dono do post alvo e quem comentou. A tela e o relatório não
 * precisam disso para explicar a falha, então o nome sai, e o texto que a persona gerou NÃO passa por aqui.
 *
 * O que sai, em ordem: o `@x`; o usuário que o fato lido da tela cita antes de "said" (usuário com ponto ou sublinhado); o usuário
 * depois de "post/perfil/conta/… de|do|da" quando tem ponto, sublinhado ou dígito (um nome comum como "post de baixo" fica); e os
 * usuários CONHECIDOS da operação (o perfil alvo que o dono digitou), em qualquer posição. Sem lista de nomes de pessoas: usuário
 * simples, sem moldura e fora dos conhecidos, não é pego (limite conhecido).
 */
const MOLDURA = /\b(post|posts|perfil|conta|usuário|usuario|autor|autora|página|pagina|comentário|comentario|foto|vídeo|video|reel|feed|o|a|os|as)(\s+(?:de|do|da)\s+)(@?[A-Za-z0-9._]{3,30})/gi;

const escapar = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function mascararTerceiros(texto: string, conhecidos: readonly string[] = []): string {
  let s = texto
    .replace(/@[A-Za-z0-9._]+/g, '@[omitido]')
    .replace(/\b[A-Za-z0-9]+(?:[._][A-Za-z0-9]+)+(?= said\b)/g, '[usuário omitido]')
    .replace(MOLDURA, (inteiro, palavra: string, de: string, usuario: string) => (/[._0-9]/.test(usuario) ? `${palavra}${de}[usuário omitido]` : inteiro));
  for (const u of conhecidos) {
    const nome = u.trim().replace(/^@/, '');
    if (nome.length >= 3) s = s.replace(new RegExp(`(?<![A-Za-z0-9._])${escapar(nome)}(?![A-Za-z0-9_])`, 'gi'), '[usuário omitido]');
  }
  return s;
}

/** Os usuários que o dono informou no pedido (o perfil alvo): o texto do backend os cita, e a tela não precisa repeti-los em claro. */
export function usuariosConhecidosDaOperacao(parametros: Readonly<Record<string, string>> | null | undefined): string[] {
  const u = parametros?.username?.trim().replace(/^@/, '');
  return u ? [u] : [];
}
