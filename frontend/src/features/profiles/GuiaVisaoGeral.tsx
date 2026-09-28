/**
 * Guia "Visão geral": a pessoa num relance — identidade, a faixa de fotos, as contas e o aparelho, a voz em réguas
 * e os números. Tudo vem do objeto da persona (que já traz voz, biografia e imagens desde a 047/048) e das contas
 * carregadas pelo shell; interações e capacidades são as únicas leituras próprias, e cada uma cai em vazio, não
 * em erro.
 */
import { ArrowRight, ImagePlus, Smartphone, Sparkles, Star } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, profileAvatarUrl } from '../../api/client';
import type { ProfileAccount, ProfileCapabilities, SocialInteraction } from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { ACCOUNT_SESSION_STATUS, INSTANCE_STATE, PROFILE_STATUS, metaOf } from '../../lib/status';
import { plural } from '../../lib/format';
import { formatAgo, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import type { Aba } from './abas';
import { politicaDe, religiaoDe, resumoDaPolitica, resumoDaReligiao } from './CrencasPersona';
import { Linha, useVersaoAoVivo } from './detalheComum';
import { EMOJI_OPTIONS, FORMALITY_OPTIONS, LENGTH_OPTIONS, Ruler, StatFigure, TagList } from './PersonaVisual';
import { handleDe, idsDosAparelhos, nomeDe, type Pessoa } from './pessoa';
import styles from './Profiles.module.css';
import { InteractionTimeline } from './Timeline';

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
  const handle = handleDe(profile);
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
          <div className={styles.identidade}>
            <Avatar src={profileAvatarUrl(profile.id)} name={nome} size={72} />
            <div>
              <h3 className={styles.title}>{nome}</h3>
              <p className={styles.lead}>
                {handle ? `@${handle}` : 'sem conta de cadastro'}{profile.email ? ` · ${profile.email}` : ''}
              </p>
            </div>
          </div>
          <dl className={styles.rows}>
            <Linha rotulo="Idade">{typeof profile.age === 'number' ? `${profile.age} anos` : '—'}</Linha>
            <Linha rotulo="Gênero">{profile.gender || '—'}</Linha>
            <Linha rotulo="Cidade">{bio.home?.city || '—'}</Linha>
            <Linha rotulo="Profissão">{bio.work?.profession || '—'}</Linha>
            {/* Crenças numa linha cada (ADR-047): o detalhe, com o espectro, fica na guia Persona. */}
            <Linha rotulo="Religião">{resumoDaReligiao(religiaoDe(bio)) || '—'}</Linha>
            <Linha rotulo="Política">{resumoDaPolitica(politicaDe(bio)) || '—'}</Linha>
            {profile.email ? <Linha rotulo="E-mail">{profile.email}</Linha> : null}
          </dl>
          <div className={styles.identityBadges}>
            <StatusBadge meta={metaOf(PROFILE_STATUS, profile.status)} />
            <Badge icon={Smartphone} tone={profile.instance_id ? 'neutral' : 'muted'}>
              {profile.instance_id
                ? `${profile.instance_id} (principal)${outros > 0 ? ` +${outros}` : ''}`
                : 'sem aparelho vinculado'}
            </Badge>
          </div>
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
            <StatFigure value={ultimoContato ? formatAgo(ultimoContato, now) : 'nunca'} label="último contato" />
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
                  {c.handle ? `@${c.handle.replace(/^@/, '')} ` : ''}
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
                {aparelho ? <StatusBadge meta={metaOf(INSTANCE_STATE, aparelho.state)} size="sm" /> : null}
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
