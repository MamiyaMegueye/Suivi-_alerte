import { useEffect, useMemo, useState } from 'react';
import { importerManuels, supprimerManuels, urlExportManuels } from '../api.js';
import { fmtDate, fmtEntier, fmtMontant, fmtPct } from '../format.js';
import { useData } from '../hooks/useData.js';
import StatusBadge from './StatusBadge.jsx';

// L'onglet a ses propres filtres : la période et le centre du haut de la page
// ne s'appliquent pas, tous les reçus importés sont vérifiés.
const SANS_FILTRE = { dateDebut: '', dateFin: '', centre: '' };
const FILTRES_VIDES = { reference: '', centre: '', du: '', au: '', statut: 'all' };
const MAX_LIGNES = 500;
const CODES = {
  found: ['MAN_RETROUVE'],
  partial: ['MAN_MONTANT'],
  missing: ['MAN_ABSENT'],
  anomalies: ['MAN_MONTANT', 'MAN_ABSENT'],
};
const BADGES = { MAN_RETROUVE: 'Retrouvé', MAN_MONTANT: 'Montant ≠', MAN_ABSENT: 'Manquant CRM' };

function Kpi({ libelle, valeur, pct, sous, niveau, children, large }) {
  return (
    <div className={`kpi${niveau ? ` kpi-${niveau}` : ''}${large ? ' kpi-large' : ''}`}>
      <div className="kpi-libelle">{libelle}</div>
      <div className="kpi-valeur">
        {valeur}
        {pct != null && <span className="kpi-pct">{fmtPct(pct)}</span>}
      </div>
      <div className="kpi-sous">{sous}</div>
      {children && <div className="kpi-actions">{children}</div>}
    </div>
  );
}

/** Fenêtre de détail d'un reçu manuel : verdict, reçu, transactions du CRM. */
function Detail({ ligne, onFermer }) {
  useEffect(() => {
    const touche = (e) => e.key === 'Escape' && onFermer();
    window.addEventListener('keydown', touche);
    return () => window.removeEventListener('keydown', touche);
  }, [onFermer]);
  const v = ligne;
  let verdict;
  if (v.resultat_code === 'MAN_RETROUVE') {
    verdict = (
      <div className="verdict verdict-bon">
        <strong>Reçu retrouvé exactement dans le CRM</strong>
        <p>
          Le paiement a bien été saisi dans le CRM avec le même montant (reçu {v.num_recu_crm} du {fmtDate(v.date_crm)}
          {v.delai_jours ? `, ${fmtEntier(v.delai_jours)} jours après le reçu manuel` : ', le même jour'}). Aucune action
          requise.
        </p>
      </div>
    );
  } else if (v.resultat_code === 'MAN_MONTANT') {
    verdict = (
      <div className="verdict verdict-serieux">
        <strong>Référence trouvée, mais le montant ne correspond pas</strong>
        <p>
          Montant du reçu manuel : <b>{fmtMontant(v.montant)} MRU</b> · Montant du reçu CRM retenu ({v.num_recu_crm}) :{' '}
          <b>{fmtMontant(v.montant_crm)} MRU</b> · Écart (CRM − manuel) : <b>{fmtMontant(v.ecart_montant)} MRU</b>. À
          vérifier : erreur de saisie ou montant partiellement enregistré.
        </p>
      </div>
    );
  } else {
    verdict = (
      <div className="verdict verdict-critique">
        <strong>Aucune trace de ce paiement dans le CRM</strong>
        <p>
          Un reçu manuel a été émis pour <b>{fmtMontant(v.montant)} MRU</b> mais aucun reçu d’espèces du CRM ne lui
          correspond pour la référence <b>{v.reference}</b>. Le paiement n’a jamais été régularisé. Action : vérifier
          auprès du centre <b>{v.nom_centre || v.centre_fichier || v.centre}</b>.
          {v.anomalie_reference ? ` Référence mal saisie : ${v.anomalie_reference}.` : ''}
        </p>
      </div>
    );
  }
  return (
    <div className="modale-fond" onClick={(e) => e.target === e.currentTarget && onFermer()}>
      <div className="modale" role="dialog" aria-modal="true" aria-label={`Reçu manuel ${v.reference}`}>
        <div className="modale-entete">
          <h2>Reçu manuel · {v.reference}</h2>
          <button type="button" className="btn btn-petit" onClick={onFermer} aria-label="Fermer">
            ×
          </button>
        </div>
        {verdict}
        <h3>Reçu manuel (centre)</h3>
        <table className="tableau">
          <thead>
            <tr>
              <th>Date</th>
              <th>Centre</th>
              <th>N° reçu</th>
              <th>Référence</th>
              <th className="num">Montant</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{fmtDate(v.date_recu)}</td>
              <td>{v.nom_centre ? `${v.centre} · ${v.nom_centre}` : v.centre_fichier || v.centre}</td>
              <td>{v.num_recu_manuel || '—'}</td>
              <td>{v.reference}</td>
              <td className="num">{fmtMontant(v.montant)}</td>
            </tr>
          </tbody>
        </table>
        <h3>Transactions CRM pour cette référence ({fmtEntier(v.nb_transactions_crm)})</h3>
        {v.transactions_crm.length === 0 ? (
          <p className="verdict verdict-critique">
            Aucune transaction CRM en espèces trouvée pour la référence <b>{v.reference}</b> depuis le{' '}
            {fmtDate(v.date_recu)}.
          </p>
        ) : (
          <table className="tableau">
            <thead>
              <tr>
                <th>Date</th>
                <th>Centre</th>
                <th>N° reçu</th>
                <th className="num">Montant</th>
                <th>Statut</th>
                <th>Agent</th>
                <th>Correspondance</th>
              </tr>
            </thead>
            <tbody>
              {v.transactions_crm.map((t) => (
                <tr key={t.num_recu} className={t.correspondance ? 'ligne-retenue' : ''}>
                  <td>{fmtDate(t.date)}</td>
                  <td>Centre {t.centre}</td>
                  <td>{t.num_recu || '—'}</td>
                  <td className="num">{fmtMontant(t.montant)}</td>
                  <td>{t.statut}</td>
                  <td>{[t.matricule, t.nom_agent].filter(Boolean).join(' · ') || '—'}</td>
                  <td>
                    {t.correspondance ? (
                      <StatusBadge
                        code={v.resultat_code}
                        libelle={v.resultat_code === 'MAN_RETROUVE' ? 'Match exact' : 'Reçu retenu'}
                      />
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/** Liste des références mal saisies (≠ 8 chiffres), dans les reçus manuels et le CRM. */
function References({ lignes, onFermer }) {
  return (
    <div className="modale-fond" onClick={(e) => e.target === e.currentTarget && onFermer()}>
      <div className="modale" role="dialog" aria-modal="true" aria-label="Références mal saisies">
        <div className="modale-entete">
          <h2>Références mal saisies ({fmtEntier(lignes.length)})</h2>
          <button type="button" className="btn btn-petit" onClick={onFermer} aria-label="Fermer">
            ×
          </button>
        </div>
        <table className="tableau">
          <thead>
            <tr>
              <th>Source</th>
              <th>Centre / Caisse</th>
              <th>Date</th>
              <th>Référence saisie</th>
              <th className="num">Montant</th>
              <th>Anomalie</th>
            </tr>
          </thead>
          <tbody>
            {lignes.map((r, i) => (
              <tr key={i}>
                <td>
                  <StatusBadge code={r.source === 'CRM' ? 'MAN_MONTANT' : 'MAN_ABSENT'} libelle={r.source} />
                </td>
                <td>{r.centre || '—'}</td>
                <td>{fmtDate(r.date)}</td>
                <td>{r.reference || '(vide)'}</td>
                <td className="num">{fmtMontant(r.montant)}</td>
                <td>{r.anomalie}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p>
          <a className="btn btn-principal" href={urlExportManuels({ partie: 'references' })} download>
            ⬇ Télécharger en Excel
          </a>
        </p>
      </div>
    </div>
  );
}

/**
 * Onglet « Reçus manuels » : import du fichier des reçus écrits à la main par
 * les centres pendant les coupures de réseau, puis recherche de chaque reçu
 * dans les encaissements en espèces du CRM.
 */
export default function Manuels({ version }) {
  const [imports, setImports] = useState(0);
  const [envoi, setEnvoi] = useState({ enCours: false, erreurs: [] });
  const [ajouts, setAjouts] = useState([]);
  const [confirmation, setConfirmation] = useState(false);
  const [f, setF] = useState(FILTRES_VIDES);
  const [detail, setDetail] = useState(null);
  const [voirReferences, setVoirReferences] = useState(false);
  const { data, chargement, erreur, attente } = useData('manuels/verification', SANS_FILTRE, version + imports);

  const importer = async (fichiers) => {
    setEnvoi({ enCours: true, erreurs: [] });
    const faits = [];
    const erreurs = [];
    for (const fichier of fichiers) {
      try {
        const reponse = await importerManuels(fichier);
        if (reponse?.manuels?.ajout) faits.push(reponse.manuels.ajout);
      } catch (err) {
        erreurs.push(`${fichier.name} : ${err.message}`);
      }
    }
    setAjouts(faits);
    setEnvoi({ enCours: false, erreurs });
    if (faits.length) setImports((n) => n + 1);
  };

  const reinitialiser = async () => {
    setConfirmation(false);
    setEnvoi({ enCours: true, erreurs: [] });
    try {
      await supprimerManuels();
      setAjouts([]);
      setF(FILTRES_VIDES);
      setEnvoi({ enCours: false, erreurs: [] });
      setImports((n) => n + 1);
    } catch (err) {
      setEnvoi({ enCours: false, erreurs: [err.message] });
    }
  };

  const info = data?.manuels ?? null;
  const t = data?.totaux;
  const toutes = data?.lignes ?? [];
  const centres = useMemo(() => {
    const vus = new Map();
    for (const l of toutes) {
      const cle = l.centre || l.centre_fichier;
      if (cle && !vus.has(cle)) vus.set(cle, l.nom_centre ? `${l.centre} · ${l.nom_centre}` : cle);
    }
    return [...vus.entries()].sort((a, b) => a[1].localeCompare(b[1], 'fr', { numeric: true }));
  }, [toutes]);
  const filtrees = useMemo(() => {
    const ref = f.reference.trim();
    const codes = CODES[f.statut];
    return toutes.filter(
      (l) =>
        (!ref || l.reference.includes(ref)) &&
        (!f.centre || (l.centre || l.centre_fichier) === f.centre) &&
        (!f.du || (l.date_recu || '') >= f.du) &&
        (!f.au || (l.date_recu || '9999') <= f.au) &&
        (!codes || codes.includes(l.resultat_code))
    );
  }, [toutes, f]);
  const changer = (champs) => setF((x) => ({ ...x, ...champs }));
  const pct = (n) => (t?.nb ? (100 * n) / t.nb : null);
  const nb = (code) => data?.synthese?.find((s) => s.code === code)?.nb ?? 0;
  const parametresExport = (statut) => ({
    reference: f.reference.trim() || undefined,
    centre: f.centre || undefined,
    du: f.du || undefined,
    au: f.au || undefined,
    statut,
  });

  return (
    <div className="comparaison">
      <div className="comparaison-outils">
        <label className="btn btn-fichier">
          {envoi.enCours ? 'Lecture des fichiers…' : info ? 'Ajouter un fichier de reçus manuels' : 'Importer les reçus manuels (Excel ou CSV)'}
          <input
            type="file"
            accept=".xlsx,.xlsm,.csv"
            multiple
            disabled={envoi.enCours}
            onChange={(e) => {
              const choisis = Array.from(e.target.files ?? []);
              e.target.value = '';
              if (choisis.length) importer(choisis);
            }}
          />
        </label>
        {info && (
          <span className="fichier-nom" title={info.fichiers.map((x) => x.nom_fichier).join(', ')}>
            {info.fichiers.length > 1 ? `${info.fichiers.length} fichiers` : info.fichiers[0].nom_fichier} ·{' '}
            {fmtEntier(info.nb_recus)} reçus
            {info.date_min ? ` · du ${fmtDate(info.date_min)} au ${fmtDate(info.date_max)}` : ''}
          </span>
        )}
        {info && !confirmation && (
          <button type="button" className="btn" disabled={envoi.enCours} onClick={() => setConfirmation(true)}>
            Réinitialiser
          </button>
        )}
        {info && confirmation && (
          <span className="confirmation" role="group" aria-label="Confirmer la réinitialisation">
            Effacer les reçus manuels importés ?
            <button type="button" className="btn btn-alerte" onClick={reinitialiser}>
              Oui, effacer
            </button>
            <button type="button" className="btn" onClick={() => setConfirmation(false)}>
              Annuler
            </button>
          </span>
        )}
      </div>

      {envoi.erreurs.map((e) => (
        <p className="alerte" role="alert" key={e}>
          {e}
        </p>
      ))}
      {erreur && !data && <p className="alerte">{erreur}</p>}
      {ajouts.map((a) => (
        <p className="info-chargement" role="status" key={a.nom_fichier}>
          {a.nom_fichier} : {fmtEntier(a.nb_recus)} reçus, {fmtMontant(a.montant)} MRU
          {a.date_min ? ` du ${fmtDate(a.date_min)} au ${fmtDate(a.date_max)}` : ''}
        </p>
      ))}
      {(attente || (info === null && chargement && !data)) && (
        <p className="info-chargement" role="status">
          {attente || 'Chargement…'} {attente ? '(lecture des encaissements du CRM, la première fois peut prendre quelques minutes)' : ''}
        </p>
      )}

      {data && !info && !envoi.enCours && (
        <p className="panneau-description">
          Aucun fichier de reçus manuels importé. Le fichier doit avoir une ligne d’en-tête avec les colonnes Centre,
          Date, N° reçu, Référence et Montant.
        </p>
      )}

      {info && t && (
        <div className={chargement ? 'en-chargement' : ''}>
          <h3 className="titre-section">Indicateurs clés</h3>
          <div className="kpi-grille">
            <Kpi libelle="Reçus analysés" valeur={fmtEntier(t.nb)} sous={`Montant total : ${fmtMontant(t.montant)} MRU`} />
            <Kpi
              libelle="Retrouvés exactement"
              niveau="bon"
              valeur={fmtEntier(nb('MAN_RETROUVE'))}
              pct={pct(nb('MAN_RETROUVE'))}
              sous="Référence + montant identiques"
            />
            <Kpi
              libelle="Montant différent"
              niveau="serieux"
              valeur={fmtEntier(t.nb_montant_different)}
              pct={pct(t.nb_montant_different)}
              sous={`Écart cumulé : ${fmtMontant(t.ecart_montant_absolu)} MRU`}
            />
            <Kpi
              libelle="Non remontés au CRM"
              niveau="critique"
              valeur={fmtEntier(t.nb_non_remontes)}
              pct={pct(t.nb_non_remontes)}
              sous={`Manque à percevoir : ${fmtMontant(t.montant_non_remonte)} MRU (${fmtPct(t.pct_non_remonte)} du montant)`}
            >
              <button type="button" className="btn btn-petit" onClick={() => setF({ ...FILTRES_VIDES, statut: 'missing' })}>
                Voir la liste
              </button>
              <a className="btn btn-petit" href={urlExportManuels({ statut: 'missing' })} download>
                Télécharger
              </a>
            </Kpi>
            <Kpi
              libelle="Base CRM (référence)"
              niveau="neutre"
              valeur={fmtEntier(t.nb_crm)}
              sous={`${fmtEntier(t.nb_abonnes_crm)} abonnés distincts · reçus d’espèces des centres du fichier`}
            />
            <Kpi
              libelle="Total à investiguer"
              niveau={t.nb_a_investiguer > 0 ? 'serieux' : 'bon'}
              valeur={fmtEntier(t.nb_a_investiguer)}
              sous={`${fmtPct(t.pct_a_investiguer)} des reçus manuels`}
            />
            <Kpi
              libelle="Références mal saisies (≠ 8 chiffres)"
              niveau={t.nb_references_mal_saisies > 0 ? 'attention' : 'bon'}
              valeur={fmtEntier(t.nb_references_mal_saisies)}
              sous={`${fmtEntier(t.nb_mal_saisies_crm)} dans le CRM · ${fmtEntier(t.nb_mal_saisies_manuels)} dans les reçus manuels`}
              large
            >
              {t.nb_references_mal_saisies > 0 && (
                <>
                  <button type="button" className="btn btn-petit" onClick={() => setVoirReferences(true)}>
                    Voir la liste
                  </button>
                  <a className="btn btn-petit" href={urlExportManuels({ partie: 'references' })} download>
                    Télécharger
                  </a>
                </>
              )}
            </Kpi>
          </div>

          <h3 className="titre-section">Recherche &amp; filtrage</h3>
          <div className="recherche-manuels">
            <label>
              Référence abonné
              <input
                className="champ"
                type="text"
                placeholder="ex : 16291290"
                value={f.reference}
                onChange={(e) => changer({ reference: e.target.value })}
              />
            </label>
            <label>
              Centre
              <select className="champ" value={f.centre} onChange={(e) => changer({ centre: e.target.value })}>
                <option value="">Tous</option>
                {centres.map(([cle, libelle]) => (
                  <option key={cle} value={cle}>
                    {libelle}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Date du
              <input className="champ" type="date" value={f.du} onChange={(e) => changer({ du: e.target.value })} />
            </label>
            <label>
              Date au
              <input className="champ" type="date" value={f.au} onChange={(e) => changer({ au: e.target.value })} />
            </label>
            <label>
              Statut
              <select className="champ" value={f.statut} onChange={(e) => changer({ statut: e.target.value })}>
                <option value="all">Tous les reçus</option>
                <option value="found">Retrouvés exactement</option>
                <option value="partial">Montant différent</option>
                <option value="missing">Non remontés au CRM</option>
                <option value="anomalies">Toutes anomalies (partiel + manquant)</option>
              </select>
            </label>
            <button type="button" className="btn" onClick={() => setF(FILTRES_VIDES)}>
              Réinitialiser
            </button>
          </div>

          <div className="sous-entete">
            <h3>
              {fmtEntier(filtrees.length)} {filtrees.length > 1 ? 'reçus' : 'reçu'} ({fmtPct(pct(filtrees.length))} des
              reçus manuels) · {fmtMontant(filtrees.reduce((s, l) => s + l.montant, 0))} MRU
            </h3>
            <span className="exports">
              <a className="btn btn-principal btn-petit" href={urlExportManuels(parametresExport(f.statut))} download>
                ⬇ Exporter Excel
              </a>
            </span>
          </div>
          {filtrees.length === 0 ? (
            <p className="panneau-description">Aucun reçu ne correspond aux filtres actuels.</p>
          ) : (
            <div className="tableau-bloc">
              <div className="tableau-defilement">
                <table className="tableau">
                  <thead>
                    <tr>
                      <th>Statut</th>
                      <th>Centre</th>
                      <th>Date du reçu</th>
                      <th>N° reçu</th>
                      <th>Réf. Abonné</th>
                      <th className="num">Montant reçu (MRU)</th>
                      <th className="num">Trouvé CRM</th>
                      <th>N° reçu CRM</th>
                      <th>Date CRM</th>
                      <th>Détail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtrees.slice(0, MAX_LIGNES).map((l, i) => (
                      <tr key={i} className="ligne-cliquable" onClick={() => setDetail(l)}>
                        <td>
                          <StatusBadge code={l.resultat_code} libelle={BADGES[l.resultat_code]} />
                        </td>
                        <td>{l.nom_centre ? `${l.centre} · ${l.nom_centre}` : l.centre_fichier || l.centre}</td>
                        <td>{fmtDate(l.date_recu)}</td>
                        <td>{l.num_recu_manuel || '—'}</td>
                        <td>{l.reference}</td>
                        <td className="num">{fmtMontant(l.montant)}</td>
                        <td className="num">{l.montant_crm == null ? '—' : fmtMontant(l.montant_crm)}</td>
                        <td>{l.num_recu_crm || '—'}</td>
                        <td>{fmtDate(l.date_crm)}</td>
                        <td className="lien-detail">Voir →</td>
                      </tr>
                    ))}
                    {filtrees.length > MAX_LIGNES && (
                      <tr>
                        <td colSpan={10} className="panneau-description">
                          … et {fmtEntier(filtrees.length - MAX_LIGNES)} autres reçus. Affinez votre recherche.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {detail && <Detail ligne={detail} onFermer={() => setDetail(null)} />}
      {voirReferences && (
        <References lignes={data?.references_mal_saisies ?? []} onFermer={() => setVoirReferences(false)} />
      )}
    </div>
  );
}
