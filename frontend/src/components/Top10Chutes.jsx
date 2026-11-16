import { useState } from 'react';
import { fmtMontant, fmtPct } from '../format.js';

/**
 * Les dix centres qui ont le plus chuté, en barres rouges, classés au choix
 * par % de chute ou par montant perdu.
 * `lignes` : [{ centre, nom_centre, avant, apres }] ; seuls les centres en baisse sont gardés.
 */
export default function Top10Chutes({ titre, lignes, libelleAvant }) {
  const [critere, setCritere] = useState('pct');
  const chutes = lignes
    .filter((l) => l.avant > 0 && l.apres < l.avant)
    .map((l) => ({ ...l, perte: l.avant - l.apres, pct: (100 * (l.apres - l.avant)) / l.avant }));
  const top = chutes
    .sort((a, b) => (critere === 'pct' ? a.pct - b.pct : b.perte - a.perte))
    .slice(0, 10);
  const mesure = (l) => (critere === 'pct' ? -l.pct : l.perte);
  const max = top.length ? mesure(top[0]) : 1;

  return (
    <figure className="top10 top10-chutes">
      <div className="graphe-entete">
        <figcaption>{titre}</figcaption>
        <div className="segment" role="group" aria-label="Classement des chutes">
          <button
            type="button"
            className={`segment-btn${critere === 'pct' ? ' actif' : ''}`}
            aria-pressed={critere === 'pct'}
            onClick={() => setCritere('pct')}
          >
            % de chute
          </button>
          <button
            type="button"
            className={`segment-btn${critere === 'montant' ? ' actif' : ''}`}
            aria-pressed={critere === 'montant'}
            onClick={() => setCritere('montant')}
          >
            Montant perdu
          </button>
        </div>
      </div>
      {top.length === 0 ? (
        <p className="top10-vide">Aucun centre en baisse.</p>
      ) : (
        <ol className="top10-liste">
          {top.map((l, i) => (
            <li
              key={l.centre}
              className="top10-ligne"
              title={`${l.centre} ${l.nom_centre ?? ''} : ${fmtMontant(Math.round(l.avant))} → ${fmtMontant(
                Math.round(l.apres)
              )} MRU (${libelleAvant} → période choisie)`}
            >
              <span className="top10-rang top10-rang-chute">{i + 1}</span>
              <span className="top10-nom">
                {l.nom_centre || `Centre ${l.centre}`}
                <small>{l.centre}</small>
              </span>
              <span className="top10-piste">
                <span className="top10-barre top10-barre-chute" style={{ width: `${Math.max(1, (100 * mesure(l)) / max)}%` }} />
              </span>
              <span className="top10-valeur top10-valeur-chute">
                {critere === 'pct' ? (
                  <>
                    {fmtPct(Math.round(l.pct * 10) / 10)}
                    <small>−{fmtMontant(Math.round(l.perte))} MRU</small>
                  </>
                ) : (
                  <>
                    <span>
                      −{fmtMontant(Math.round(l.perte))} <small className="unite">MRU</small>
                    </span>
                    <small>{fmtPct(Math.round(l.pct * 10) / 10)}</small>
                  </>
                )}
              </span>
            </li>
          ))}
        </ol>
      )}
    </figure>
  );
}
