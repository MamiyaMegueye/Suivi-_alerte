import { useEffect, useState } from 'react';
import { getJSON } from '../api.js';
import { fmtMontant } from '../format.js';

export default function ModalChutes({ chutes, onFermer }) {
  const [secteurActif, setSecteurActif] = useState(null);
  const [detail, setDetail] = useState(null);
  const [chargement, setChargement] = useState(false);

  useEffect(() => {
    if (!secteurActif) { setDetail(null); return; }
    setChargement(true);
    getJSON(`chutes_secteur/detail/${secteurActif}`)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setChargement(false));
  }, [secteurActif]);

  // Fermer avec Escape
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onFermer(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onFermer]);

  const tous = chutes.total ?? [];

  return (
    <div className="modal-fond" onClick={onFermer}>
      <div className="modal-chutes" onClick={(e) => e.stopPropagation()}>
        <div className="modal-entete">
          <h2>
            {secteurActif
              ? `Secteur ${secteurActif} — Détail abonnés`
              : `Chutes d'encaissement par secteur`}
          </h2>
          <span className="modal-periode">
            {chutes.mois_actuel} (j1–j{chutes.jour_compare}) vs {chutes.mois_reference}
          </span>
          <button type="button" className="modal-fermer" onClick={onFermer} aria-label="Fermer">✕</button>
        </div>

        {!secteurActif ? (
          <div className="modal-corps">
            <table className="tableau-chutes">
              <thead>
                <tr>
                  <th>Secteur</th>
                  <th className="num">Montant M</th>
                  <th className="num">Montant M-2</th>
                  <th className="num">Variation</th>
                </tr>
              </thead>
              <tbody>
                {tous.map((s) => (
                  <tr
                    key={s.secteur}
                    className={`ligne-secteur${s.variation_pct !== null && s.variation_pct <= -20 ? ' chute' : ''}`}
                    onClick={() => setSecteurActif(s.secteur)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter') setSecteurActif(s.secteur); }}
                    title="Cliquer pour voir le détail des abonnés"
                  >
                    <td>{s.secteur}</td>
                    <td className="num">{fmtMontant(s.montant_actuel)}</td>
                    <td className="num">{fmtMontant(s.montant_ref)}</td>
                    <td className={`num${s.variation_pct !== null && s.variation_pct <= -20 ? ' negatif' : s.variation_pct > 0 ? ' positif' : ''}`}>
                      {s.variation_pct !== null ? `${s.variation_pct} %` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="modal-corps">
            <button type="button" className="btn btn-retour" onClick={() => setSecteurActif(null)}>
              ← Retour aux secteurs
            </button>
            {chargement ? (
              <p className="info-chargement">Chargement…</p>
            ) : detail ? (
              <table className="tableau-chutes">
                <thead>
                  <tr>
                    <th>Référence</th>
                    <th className="num">Montant M</th>
                    <th className="num">Montant M-2</th>
                    <th className="num">Variation</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.abonnes.map((a) => (
                    <tr key={a.reference}>
                      <td>{a.reference}</td>
                      <td className="num">{fmtMontant(a.montant_actuel)}</td>
                      <td className="num">{fmtMontant(a.montant_ref)}</td>
                      <td className={`num${a.variation_pct !== null && a.variation_pct <= -20 ? ' negatif' : a.variation_pct > 0 ? ' positif' : ''}`}>
                        {a.variation_pct !== null ? `${a.variation_pct} %` : 'Nouveau'}
                      </td>
                    </tr>
                  ))}
                  {detail.abonnes.length === 0 && (
                    <tr><td colSpan={4} style={{ textAlign: 'center' }}>Aucun abonné trouvé</td></tr>
                  )}
                </tbody>
              </table>
            ) : (
              <p className="alerte">Erreur de chargement</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
