import { useEffect, useRef, useState } from 'react';

// Le serveur ferme chaque flux au bout de quelques secondes et le navigateur
// se reconnecte seul : une coupure aussi courte n'est pas signalée.
const DELAI_COUPURE = 8000;

/**
 * Connexion au flux temps réel (Server-Sent Events).
 * `version` augmente à chaque rafraîchissement du serveur : les écrans
 * rechargent alors leurs données.
 */
export function useLive() {
  const [etat, setEtat] = useState({
    connecte: false,
    baseOk: null,
    erreur: null,
    dernier: null,
    intervalle: null,
    version: 0,
  });
  const dernierVu = useRef(null);

  useEffect(() => {
    const flux = new EventSource('/api/stream');
    let coupure = null;

    const appliquer = (ev) => {
      let m;
      try {
        m = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (coupure) {
        clearTimeout(coupure);
        coupure = null;
      }
      // On ne recharge les écrans que si le serveur a rafraîchi ses données
      // depuis le dernier message reçu.
      const nouveau = dernierVu.current !== null && m.dernier_rafraichissement !== dernierVu.current;
      dernierVu.current = m.dernier_rafraichissement ?? dernierVu.current;
      setEtat((e) => ({
        connecte: true,
        baseOk: m.base_ok,
        erreur: m.erreur,
        dernier: m.dernier_rafraichissement,
        intervalle: m.intervalle_secondes,
        version: e.version + (nouveau ? 1 : 0),
      }));
    };

    flux.addEventListener('hello', appliquer);
    flux.addEventListener('refresh', appliquer);
    flux.onerror = () => {
      if (!coupure) {
        coupure = setTimeout(() => setEtat((e) => ({ ...e, connecte: false })), DELAI_COUPURE);
      }
    };

    return () => {
      if (coupure) clearTimeout(coupure);
      flux.close();
    };
  }, []);

  return etat;
}
