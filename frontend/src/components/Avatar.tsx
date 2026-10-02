import { useState } from 'react';
import { cx } from '../lib/format';
import ui from './Avatar.module.css';

interface AvatarProps {
  /** Endereço da foto, ou `undefined` quando o perfil não tem (`profileAvatarUrl(id, has_avatar)`): aí são as
   *  iniciais, sem requisição (a rota responde 404 sem foto). Se a foto falhar ao carregar, também caem as iniciais. */
  src?: string;
  /** Nome de exibição, usado para as iniciais e para o texto alternativo. */
  name: string;
  size?: number;
}

function iniciais(nome: string): string {
  const partes = nome.trim().split(/\s+/).filter(Boolean);
  if (partes.length === 0) return '?';
  const primeira = partes[0]?.[0] ?? '';
  const ultima = partes.length > 1 ? partes[partes.length - 1]?.[0] ?? '' : '';
  return (primeira + ultima).toUpperCase();
}

/** Foto redonda, como a do Instagram. Sem foto (ou se ela falhar ao carregar), mostra as iniciais no mesmo
 *  círculo — nunca um quadrado quebrado nem um vazio do tamanho da imagem. */
export function Avatar({ src, name, size = 40 }: AvatarProps) {
  const [falhou, setFalhou] = useState(false);
  const estilo = { width: size, height: size, fontSize: Math.max(13, Math.round(size * 0.38)) };
  if (!src || falhou) {
    return (
      <span className={cx(ui.avatar, ui.fallback)} style={estilo} aria-hidden>
        {iniciais(name)}
      </span>
    );
  }
  return (
    <img
      className={ui.avatar}
      style={estilo}
      src={src}
      alt={`Foto de ${name}`}
      loading="lazy"
      onError={() => setFalhou(true)}
    />
  );
}
