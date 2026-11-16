import { fmtMontant, fmtPct } from '../format.js';

/**
 * Les dix centres qui encaissent le plus, en barres horizontales triées.
 * `lignes` : [{ centre, nom_centre, montant }] ; `total` : encaissé de tous les centres.
 */
export default function Top10({ titre, lignes, total, children, moins = false }) {
  const top = [...lignes]
    .filter((l) => l.montant > 0)
    .sort((a, b) => (moins ? a.montant - b.montant : b.montant - a.montant))
    .slice(0, 10);
  // Les barres gardent la même échelle : la plus longue est le plus gros montant affiché.
  const max = top.length ? Math.max(...top.map((l) => l.montant)) : 1;

  return (
    <figure className={`top10${moins ? ' top10-moins' : ''}`}>
      <div className="graphe-entete">
        <figcaption>{titre}</figcaption>
        {children}
      </div>
      {top.length === 0 ? (
        <p className="top10-vide">Aucun encaissement.</p>
      ) : (
        <ol className="top10-liste">
          {top.map((l, i) => {
            const part = total ? (100 * l.montant) / total : null;
            return (
              <li
                key={l.centre}
                className="top10-ligne"
                title={`${l.centre} ${l.nom_centre ?? ''} : ${fmtMontant(l.montant)} MRU (${fmtPct(part)} du total)`}
              >
                <span className={`top10-rang${i < 3 ? ' top10-podium' : ''}`}>{i + 1}</span>
                <span className="top10-nom">
                  {l.nom_centre || `Centre ${l.centre}`}
                  <small>{l.centre}</small>
                </span>
                <span className="top10-piste">
                  <span className="top10-barre" style={{ width: `${Math.max(1, (100 * l.montant) / max)}%` }} />
                </span>
                <span className="top10-valeur">
                  {fmtMontant(Math.round(l.montant))} <small>MRU</small>
                  <em>{fmtPct(part === null ? null : Math.round(part * 10) / 10)}</em>
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </figure>
  );
}
