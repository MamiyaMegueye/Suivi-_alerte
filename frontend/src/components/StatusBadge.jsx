import { GRAVITE, LIBELLES_STATUT } from '../datasets.js';

// Une forme différente par niveau : le statut ne repose jamais sur la couleur seule.
const ICONES = {
  bon: <path d="M3.2 8.4l3 3 6.6-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />,
  attention: <path d="M8 2.2l6.4 11.2H1.6z" fill="currentColor" />,
  serieux: <path d="M8 1.6l6.4 6.4L8 14.4 1.6 8z" fill="currentColor" />,
  critique: <path d="M3.5 3.5l9 9m0-9l-9 9" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />,
  neutre: <circle cx="8" cy="8" r="4" fill="currentColor" />,
};

export default function StatusBadge({ code, libelle, infobulle }) {
  const niveau = GRAVITE[code] || 'neutre';
  const texte = libelle || LIBELLES_STATUT[code] || code || '—';
  return (
    <span className="badge" title={infobulle || texte}>
      <svg className={`badge-icone niveau-${niveau}`} viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
        {ICONES[niveau]}
      </svg>
      <span>{texte}</span>
    </span>
  );
}
