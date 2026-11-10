// Gagarine : la scène de la jauge (jouée une fois à l'ouverture, rejouable) et les boutons « Copier ».
(function () {
  'use strict';

  var reduit = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var scene = document.querySelector('.scene');
  var compteur = document.getElementById('compteur');
  var coupable = document.getElementById('coupable');
  var zramRam = document.getElementById('zram-ram');
  var range = document.getElementById('range');
  var etapes = document.querySelectorAll('#etapes li');
  var rejouer = document.getElementById('rejouer');
  var minuteurs = [];
  var animation = null;

  function etape(n) {
    etapes.forEach(function (li) {
      var k = Number(li.dataset.etape);
      li.classList.toggle('actif', k === n);
      li.classList.toggle('fait', k < n);
    });
  }

  // Le compteur suit la jauge : de « depuis » à « vers » en « duree » ms
  function compter(depuis, vers, duree, courbe) {
    if (animation) cancelAnimationFrame(animation);
    var debut = null;
    function pas(t) {
      if (debut === null) debut = t;
      var p = Math.min(1, (t - debut) / duree);
      compteur.textContent = Math.round(depuis + (vers - depuis) * courbe(p));
      scene.classList.toggle('alerte', Number(compteur.textContent) >= 95);
      if (p < 1) animation = requestAnimationFrame(pas);
    }
    animation = requestAnimationFrame(pas);
  }
  var accelere = function (p) { return p * p * p; };
  var douce = function (p) { return p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; };

  function plusTard(ms, f) { minuteurs.push(setTimeout(f, ms)); }

  function etatInitial() {
    minuteurs.forEach(clearTimeout); minuteurs = [];
    coupable.classList.remove('fige');
    [coupable, zramRam, range].forEach(function (e) { e.style.transition = 'none'; e.style.setProperty('--l', '0%'); });
    void coupable.offsetWidth;                // applique l'état sans transition
    [coupable, zramRam, range].forEach(function (e) { e.style.transition = ''; });
    compteur.textContent = '55';
    scene.classList.remove('alerte');
    coupable.querySelector('span').textContent = 'un test qui fuit';
    etape(1);
  }

  function etatFinal() {
    coupable.classList.add('fige');
    coupable.style.setProperty('--l', '0%');
    zramRam.style.setProperty('--l', '11%');
    range.style.setProperty('--l', '38%');
    compteur.textContent = '66';
    scene.classList.remove('alerte');
    etape(5);                                 // toutes les étapes affichées comme faites
  }

  function jouer() {
    etatInitial();
    rejouer.hidden = true;
    plusTard(400, function () {
      coupable.style.setProperty('--l', '40%');
      compter(55, 95, 2800, accelere);
    });
    plusTard(3300, function () {
      coupable.classList.add('fige');
      coupable.querySelector('span').textContent = 'figé';
      etape(2);
    });
    plusTard(4400, function () {
      etape(3);
      coupable.style.setProperty('--l', '0%');
      zramRam.style.setProperty('--l', '11%');
      range.style.setProperty('--l', '38%');
      compter(95, 66, 900, douce);
    });
    plusTard(5400, function () {            // valeur finale fixée, même si le navigateur a ralenti l'animation
      if (animation) cancelAnimationFrame(animation);
      compteur.textContent = '66';
      scene.classList.remove('alerte');
    });
    plusTard(5600, function () { etape(4); });
    plusTard(6400, function () { etape(5); rejouer.hidden = false; });
  }

  if (scene) {
    if (reduit) {
      etatFinal();
    } else {
      var lancer = function () { setTimeout(jouer, 300); };
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(lancer); else lancer();
      rejouer.addEventListener('click', jouer);
    }
  }

  // Boutons « Copier »
  document.querySelectorAll('.copier').forEach(function (bouton) {
    bouton.addEventListener('click', function () {
      var texte = document.getElementById(bouton.dataset.cible).textContent;
      var fini = function () {
        bouton.textContent = 'Copié';
        bouton.classList.add('copie');
        setTimeout(function () { bouton.textContent = 'Copier'; bouton.classList.remove('copie'); }, 2000);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(texte).then(fini, function () { selectionner(bouton); });
      } else {
        selectionner(bouton);
      }
    });
  });
  function selectionner(bouton) {
    var r = document.createRange();
    r.selectNodeContents(document.getElementById(bouton.dataset.cible));
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  }
})();
