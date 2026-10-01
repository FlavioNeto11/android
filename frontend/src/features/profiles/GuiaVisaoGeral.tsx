/**
 * Guia "Visão geral": a pessoa num relance — atributos, a faixa de fotos, as contas e o aparelho, a voz em réguas
 * e os números. Foto, nome, @, estado e aparelho moram UMA vez só, no cabeçalho da persona (`PersonaHeader`); o cartão
 * "Identidade" daqui tem só os atributos, e os sensíveis (religião, política) ficam recolhidos. Tudo vem do objeto da persona (que já traz voz, biografia e imagens desde a 047/048) e das contas
 * carregadas pelo shell; interações e capacidades são as únicas leituras próprias, e cada uma cai em vazio, não
 * em erro.
 */
import { ArrowRight, ChevronRight, ImagePlus, Smartphone, Sparkles, Star } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import { api } from '../../api/client';
import type { ProfileAccount, ProfileCapabilities, SocialInteraction } from '../../api/types';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { ACCOUNT_SESSION_STATUS, metaOf } from '../../lib/status';
import { plural } from '../../lib/format';
import { tempoRelativo, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { seloDoAparelho } from '../devices/selos';
import type { Aba } from './abas';
import { politicaDe, religiaoDe, resumoDaPolitica, resumoDaReligiao } from './CrencasPersona';
import { Linha, useVersaoAoVivo } from './detalheComum';
import { EMOJI_OPTIONS, FORMALITY_OPTIONS, LENGTH_OPTIONS, Ruler, StatFigure, TagList } from './PersonaVisual';
import { handleDe, idsDosAparelhos, nomeDe, rotuloDoIdentificador, type Pessoa } from './pessoa';
import styles from './Profiles.module.css';
import { InteractionTimeline } from './Timeline';

/**
 * Bloco recolhível, FECHADO por padrão: religião e política só aparecem para quem pede. O conteúdo nem é montado
 * enquanto está fechado (não fica no DOM para leitor de tela nem para cópia). Botão com `aria-expanded`, que o
 * teclado alcança e aciona com Enter ou Espaço.
 */
function AtributosDePersonalidade({ children }: { children: React.ReactNode }) {
  const [aberto, setAberto] = useState(false);
  const idCorpo = useId();
  return (
    <div className={styles.atributosSensiveis}>
      <button type="button" className={styles.atributosBotao} aria-expanded={aberto} aria-controls={idCorpo}
              onClick={() => setAberto((a) => !a)}>
        <ChevronRight size={14} className={styles.atributosSeta} data-aberto={aberto || undefined} aria-hidden />
        Atributos de personalidade
        <span className={styles.muted}>religião e política</span>
      </button>
      <div id={idCorpo} hidden={!aberto}>
        {aberto ? <dl className={styles.rows}>{children}</dl> : null}
      </div>
    </div>
  );
}

export function VisaoGeral({ profile, contas, irPara }: {
  profile: Pessoa;
  contas: ProfileAccount[] | null;
  irPara: (aba: Aba) => void;
}) {
  const [interacoes, setInteracoes] = useState<SocialInteraction[] | null>(null);
  const [capacidades, setCapacidades] = useState<ProfileCapabilities | null>(null);
  const now = useNow();
  const versao = useVersaoAoVivo(profile);
  const aparelho = useAppStore((s) => (profile.instance_id ? s.instances[profile.instance_id] : undefined));
  const workers = useAppStore((s) => s.workers);
  // N:N (v0.29): quantos aparelhos e qual é o principal (`instance_id`), sem repetir o aparelho de dois apps.
  const aparelhos = idsDosAparelhos(profile);
  const outros = aparelhos.length - (profile.instance_id ? 1 : 0);

  useEffect(() => {
    let vivo = true;
    api.listInteractions(profile.id)
      .then((r) => { if (vivo) setInteracoes(r); })
      .catch(() => { if (vivo) setInteracoes([]); });
    api.profileCapabilities(profile.id)
      .then((r) => { if (vivo) setCapacidades(r); })
      .catch(() => {
        if (vivo) setCapacidades({ profile_id: profile.id, flows: [], steps_driven_by: {}, recipe_share: null, interactions: {} });
      });
    return () => { vivo = false; };
  }, [profile.id, versao]);

  const nome = nomeDe(profile);
  const handleDaPersona = handleDe(profile);
  const t = profile.traits ?? {};
  const temVoz = !!(t.formality || t.typical_length || t.emojis || (t.interests ?? []).length);
  const fotos = (profile.images ?? []).filter((i) => i.status === 'ready');
  const totalInteracoes = capacidades ? Object.values(capacidades.interactions).reduce((a, b) => a + b, 0) : null;
  const ultimoContato = interacoes && interacoes.length > 0 ? interacoes[0]?.occurred_at ?? null : null;
  const semIaPct = capacidades?.recipe_share == null ? null : Math.round(capacidades.recipe_share * 100);
  const bio = profile.biography ?? {};

  return (
    <div className={styles.stack}>
      <Card>
        <CardHeader title="Identidade" />
        <CardBody className={styles.identityCard}>
          <dl className={styles.rows}>
            <Linha rotulo="Idade">{typeof profile.age === 'number' ? `${profile.age} anos` : '—'}</Linha>
            <Linha rotulo="Gênero">{profile.gender || '—'}</Linha>
            <Linha rotulo="Cidade">{bio.home?.city || '—'}</Linha>
            <Linha rotulo="Profissão">{bio.work?.profession || '—'}</Linha>
            {profile.email ? <Linha rotulo="E-mail">{profile.email}</Linha> : null}
          </dl>
          {/* Crenças (ADR-048) são sensíveis: não abrem por padrão. O detalhe, com o espectro, fica na guia Persona. */}
          <AtributosDePersonalidade>
            <Linha rotulo="Religião">{resumoDaReligiao(religiaoDe(bio)) || '—'}</Linha>
            <Linha rotulo="Política">{resumoDaPolitica(politicaDe(bio)) || '—'}</Linha>
          </AtributosDePersonalidade>
          {temVoz ? (
            <>
              <div className={styles.pair}>
                <Ruler compact label="Formalidade" options={FORMALITY_OPTIONS} value={t.formality} />
                <Ruler compact label="Tamanho" options={LENGTH_OPTIONS} value={t.typical_length} />
              </div>
              <Ruler compact label="Emoji" options={EMOJI_OPTIONS} value={t.emojis} />
              {(t.interests ?? []).length > 0 ? <TagList items={t.interests ?? []} /> : null}
            </>
          ) : null}
          <div className={styles.statRow}>
            <StatFigure value={totalInteracoes ?? '—'} label="interações confirmadas" />
            <StatFigure value={semIaPct === null ? '—' : `${semIaPct}%`} label="roda sem IA" />
            <StatFigure value={ultimoContato ? tempoRelativo(ultimoContato, now) : 'nunca'} label="último contato" />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Fotos" subtitle="A principal é o avatar da persona."
                    actions={<Button size="sm" variant="ghost" icon={ArrowRight} onClick={() => irPara('imagens')}>Ver em Imagens</Button>} />
        <CardBody>
          {fotos.length === 0 ? (
            <p className={styles.detail}><ImagePlus size={14} aria-hidden /> Nenhuma foto pronta ainda.</p>
          ) : (
            <ul className={styles.imageStrip} aria-label="Fotos da persona">
              {fotos.map((f, k) => (
                <li key={f.id} className={styles.imageStripItem} data-primary={f.is_primary || undefined}>
                  <img src={f.url} alt={`Foto ${k + 1} de ${nome}${f.is_primary ? ' (principal)' : ''}`} loading="lazy" />
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Contas e aparelhos"
                    actions={
                      <div className={styles.headerButtons}>
                        <Button size="sm" variant="ghost" onClick={() => irPara('contas')}>Contas e acesso</Button>
                        <Button size="sm" variant="ghost" onClick={() => irPara('aparelhos')}>Aparelhos</Button>
                      </div>
                    } />
        <CardBody>
          {contas === null ? <Skeleton height={40} /> : contas.length === 0 ? (
            <p className={styles.detail}>Nenhuma conta ainda.</p>
          ) : (
            <dl className={styles.rows}>
              {contas.map((c) => (
                <Linha key={c.id} rotulo={`${c.app_name ?? c.app_id}${c.host ? ` · ${c.host}` : ''}`}>
                  {/* O @ da persona já está no cabeçalho: aqui só entra o identificador que é de OUTRA conta. */}
                  {c.handle && c.handle !== handleDaPersona ? `${rotuloDoIdentificador(c.handle)} ` : ''}
                  <StatusBadge meta={metaOf(ACCOUNT_SESSION_STATUS, c.session?.status ?? c.session_status)} size="sm" />
                </Linha>
              ))}
            </dl>
          )}
          <p className={styles.detail}>
            <Smartphone size={13} aria-hidden />{' '}
            {profile.instance_id ? (
              <>
                {plural(aparelhos.length, 'aparelho', 'aparelhos')} · <Star size={12} aria-hidden /> principal{' '}
                <span className="mono">{profile.instance_id}</span>{' '}
                {aparelho ? <StatusBadge meta={seloDoAparelho(aparelho, workers)} size="sm" /> : null}
                {outros > 0 ? <span className={styles.muted}> · também em {aparelhos.filter((a) => a !== profile.instance_id).join(', ')}</span> : null}
              </>
            ) : 'Sem aparelho vinculado.'}
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Interações recentes" subtitle="As 8 mais recentes — a lista completa fica na guia Interações." />
        <CardBody>
          {interacoes === null ? <Skeleton height={80} /> : interacoes.length === 0 ? (
            <p className={styles.detail}>Nenhuma interação registrada ainda.</p>
          ) : (
            <InteractionTimeline itens={interacoes} limite={8} />
          )}
        </CardBody>
      </Card>
      {profile.generation?.source ? (
        <p className={styles.detail}>
          <Sparkles size={13} aria-hidden /> Origem: {profile.generation.source === 'ai'
            ? `gerada por IA${profile.generation.model ? ` (${profile.generation.model})` : ''}`
            : profile.generation.source === 'legacy_persona' ? 'persona antiga incorporada' : 'criada à mão'}
          {profile.generation.at ? ` em ${profile.generation.at.slice(0, 10)}` : ''}.
        </p>
      ) : null}
    </div>
  );
}
