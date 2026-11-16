import { useEffect, useState } from 'react';
import { getJSON } from '../api.js';

const DELAI_NOUVEL_ESSAI = 2500;

/**
 * Charge un jeu de données. Pendant un rechargement, les données précédentes
 * restent affichées (pas de saut d'écran).
 *
 * Quand le serveur répond « chargement en cours » (lecture dans Oracle),
 * `attente` contient son message et la demande est refaite toutes les
 * quelques secondes jusqu'à l'arrivée des données.
 */
export function useData(chemin, filtres, version, extra = null) {
  const [etat, setEtat] = useState({ data: null, chargement: true, erreur: null, attente: null });
  // `extra` : paramètres en plus de la période et du centre (texte recherché).
  const suite = extra ? JSON.stringify(extra) : '';
  const cle = `${chemin}|${filtres?.dateDebut ?? ''}|${filtres?.dateFin ?? ''}|${filtres?.centre ?? ''}|${suite}`;

  useEffect(() => {
    const ctrl = new AbortController();
    let minuterie = null;

    const demander = () => {
      getJSON(chemin, filtres, ctrl.signal, extra || {})
        .then((reponse) => {
          if (reponse?.enChargement) {
            setEtat((e) => ({ ...e, chargement: true, erreur: null, attente: reponse.message }));
            minuterie = setTimeout(demander, DELAI_NOUVEL_ESSAI);
          } else {
            setEtat({ data: reponse, chargement: false, erreur: null, attente: null });
          }
        })
        .catch((err) => {
          if (err.name === 'AbortError') return;
          setEtat((e) => ({ ...e, chargement: false, erreur: err.message, attente: null }));
        });
    };

    // Changement de période ou de centre : les anciennes données ne valent plus.
    setEtat((e) => ({ ...e, chargement: true, attente: null }));
    demander();
    return () => {
      ctrl.abort();
      if (minuterie) clearTimeout(minuterie);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cle, version]);

  return etat;
}

/** Retarde une valeur : évite une demande au serveur à chaque touche tapée. */
export function useRetard(valeur, delai = 400) {
  const [retardee, setRetardee] = useState(valeur);
  useEffect(() => {
    const m = setTimeout(() => setRetardee(valeur), delai);
    return () => clearTimeout(m);
  }, [valeur, delai]);
  return retardee;
}
