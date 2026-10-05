import { hashDe } from '../../lib/rotas';
import { Badge } from '../../components/Badge';
import styles from './Profiles.module.css';

/** 29.81: o porquê do rótulo de IA da imagem da publicação, gravado pela central na etapa. */
export type MotivoDoRotuloIa = 'ia' | 'foto_real' | 'nao_informado';

/** O selo do rótulo de IA nos itens que pedem o sim do dono (prévia da porta, textos da execução, aprovações da
 *  persona). Três estados (29.81): com rótulo; sem rótulo porque o dono disse que é foto real; e sem rótulo porque
 *  ninguém disse — esse último é aviso, com o caminho para marcar a foto na guia Imagens daquela persona. Sem o porquê
 *  (etapa gravada antes do 29.81) o "sem rótulo" também fica em aviso: não dá para saber se alguém disse. */
export function SeloRotuloIa({ rotulo, motivo, profileId, longo = false }: {
  rotulo: boolean | null | undefined;
  motivo?: MotivoDoRotuloIa | null;
  profileId?: string | null;
  /** O texto por extenso (a guia Aprovações da persona usa frase; as listas densas, o curto). */
  longo?: boolean;
}) {
  if (rotulo === true) {
    return <Badge tone="neutral">{longo ? 'Sai com o rótulo de IA do Instagram' : 'com rótulo de IA'}</Badge>;
  }
  if (rotulo !== false) return null;
  if (motivo === 'foto_real') {
    return <Badge tone="neutral">{longo ? 'Sai sem rótulo de IA (foto real, informado por você)' : 'sem rótulo de IA (foto real, informado por você)'}</Badge>;
  }
  return (
    <>
      <Badge tone="warning">
        {longo ? 'Sai sem rótulo de IA: ninguém informou se a foto é de IA' : 'sem rótulo de IA: ninguém informou se a foto é de IA'}
      </Badge>
      {profileId ? (
        <a className={styles.linkAlvo} href={hashDe('personas', { segmentos: [profileId, 'imagens'] })}>
          Informar na guia Imagens da persona
        </a>
      ) : null}
    </>
  );
}
