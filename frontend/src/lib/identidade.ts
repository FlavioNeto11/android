/**
 * O nome da IA da Central (decisão do dono, 03/10; item 29.57). Espelha `backend/app/contracts/identidade.py`, e um
 * teste do backend confere que os dois dizem o mesmo nome.
 *
 * Usado onde a IA fala com a pessoa e precisa se identificar: a prévia, as perguntas e as recusas. Não troca todo
 * rótulo "IA" do painel, e nunca entra em conteúdo publicado pelas personas.
 */
export const NOME_DA_IA = 'ANA';
