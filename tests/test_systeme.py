"""Tests de l'accès au système : mesures, rangement, bridage, notifications."""
import os
import signal
import tempfile
import unittest
from pathlib import Path

from gagarine import reglages as rg
from gagarine import systeme as sy

Mo = sy.Mo
from gagarine import cli


class TestMesures(unittest.TestCase):
    def test_ram_depuis_meminfo(self):
        t = 'MemTotal:       14000000 kB\nMemFree: 1 kB\nMemAvailable:    7000000 kB\n'
        self.assertAlmostEqual(sy.ram_depuis_meminfo(t), 50.0)

    def test_lire_processus_du_poste(self):
        procs = sy.lire_processus(os.getuid())
        self.assertIn(os.getppid() if os.getppid() in procs else os.getpid() + 0, list(procs) + [os.getppid()])
        self.assertNotIn(os.getpid(), procs)
        self.assertNotIn(1, procs)
        self.assertNotIn(2, procs)
        self.assertTrue(all(p.uid == os.getuid() for p in procs.values()))
        mien = sy.lire_processus(os.getuid(), moi=-1)
        self.assertIn(os.getpid(), mien)
        self.assertGreater(mien[os.getpid()].rss, 0)

    def test_demarrage_et_commande_de_soi(self):
        self.assertTrue(sy.demarrage(os.getpid()))
        self.assertIsNone(sy.demarrage(2 ** 22 + 5))
        self.assertTrue(sy.commande(os.getpid()))


class TestRangement(unittest.TestCase):
    MAPS = ('55d0-55e0 r--p 00000000 08:01 1 /usr/bin/x\n'
            '55e0-55f0 r-xp 00000000 08:01 1 /usr/bin/x\n'
            '55f0-5600 rw-p 00000000 08:01 1 /usr/bin/x\n'
            '5600-5800 rw-p 00000000 00:00 0 [heap]\n'
            '7f00-7f80 rw-p 00000000 00:00 0 \n'
            '7f80-7fa0 rw-s 00000000 00:01 5 /dev/shm/x\n'
            '7ffc-7fff rw-p 00000000 00:00 0 [stack]\n'
            '7fff-8000 r--p 00000000 00:00 0 [vvar]\n'
            '8000-8001 rw-p 00000000 00:00 0 [vdso]\n'
            'ffffffffff600000-ffffffffff601000 --xp 00000000 00:00 0 [vsyscall]\n')

    def test_zones_retenues(self):
        z = sy.zones_a_ranger(self.MAPS)
        self.assertEqual(z, [(0x55f0, 0x5600), (0x5600, 0x5800), (0x7f00, 0x7f80), (0x7ffc, 0x7fff)])

    def test_ranger_precis_paquets_de_1024(self):
        maps = ''.join(f'{0x1000 * i:x}-{0x1000 * i + 0x800:x} rw-p 00000000 00:00 0 \n' for i in range(1, 2501))
        appels = []

        def appel(num, *args):
            appels.append((num, [getattr(a, 'value', a) for a in args]))
            return 100 if num == sy.SYS_PROCESS_MADVISE else 77
        fermes = []
        total = sy.ranger_precis(42, appel, lambda p: maps, fermes.append)
        self.assertEqual(appels[0], (434, [42, 0]))
        madv = [a for a in appels if a[0] == 440]
        self.assertEqual([a[1][2] for a in madv], [1024, 1024, 452])
        self.assertTrue(all(a[1][0] == 77 and a[1][3] == 21 and a[1][4] == 0 for a in madv))
        self.assertEqual(total, 300)
        self.assertEqual(fermes, [77])

    def test_iovec_construit_depuis_les_zones(self):
        vus = []

        def appel(num, *args):
            if num == sy.SYS_PROCESS_MADVISE:
                import ctypes
                tab = ctypes.cast(args[1], ctypes.POINTER(sy.Iovec))
                vus.extend((tab[i].base, tab[i].longueur) for i in range(args[2].value))
            return 3
        sy.ranger_precis(1, appel, lambda p: self.MAPS, lambda fd: None)
        self.assertEqual(vus, [(0x55f0, 0x10), (0x5600, 0x200), (0x7f00, 0x80), (0x7ffc, 3)])

    def test_refus_des_le_premier_paquet_leve(self):
        def appel(num, *args):
            if num == sy.SYS_PROCESS_MADVISE:
                raise PermissionError(1, 'refusé')
            return 5
        fermes = []
        with self.assertRaises(OSError):
            sy.ranger_precis(1, appel, lambda p: self.MAPS, fermes.append)
        self.assertEqual(fermes, [5])

    def test_pidfd_open_refuse_leve(self):
        def appel(num, *args):
            raise OSError(38, 'ENOSYS')
        with self.assertRaises(OSError):
            sy.ranger_precis(1, appel, lambda p: self.MAPS, lambda fd: None)

    def test_aucune_zone_leve(self):
        with self.assertRaises(OSError):
            sy.ranger_precis(1, lambda *a: 1, lambda p: '', lambda fd: None)

    def test_repli_sur_memory_reclaim_sans_privileges(self):
        s = sy.Systeme(rg.Reglages(), root=False)
        with tempfile.TemporaryDirectory() as d:
            s.ranger(1, 12345, Path(d))   # écrit memory.reclaim dans le dossier d'origine
            self.assertEqual((Path(d) / 'memory.reclaim').read_text(), '12345')

    def test_repli_si_process_madvise_echoue_en_root(self):
        s = sy.Systeme(rg.Reglages(), root=True)
        vieux = sy.ranger_precis
        sy.ranger_precis = lambda pid: (_ for _ in ()).throw(OSError(1, 'non'))
        try:
            with tempfile.TemporaryDirectory() as d:
                s.ranger(1, 999, Path(d))
                self.assertEqual((Path(d) / 'memory.reclaim').read_text(), '999')
        finally:
            sy.ranger_precis = vieux


class TestBridageEtNotifications(unittest.TestCase):
    CG_USER = '0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-x.scope\n'
    CG_SYS = '0::/system.slice/ssh.service\n'

    def test_root_sous_user_service(self):
        c = sy.commande_bridage(42, self.CG_USER, True, 256, 'u.scope', nom_de_uid=lambda u: 'moi')
        self.assertEqual(c[:5], ['busctl', '--machine=moi@.host', '--user', 'call', 'org.freedesktop.systemd1'])
        self.assertIn('StartTransientUnit', c)

    def test_root_autre_processus_gestionnaire_systeme(self):
        c = sy.commande_bridage(42, self.CG_SYS, True, 256, 'u.scope')
        self.assertEqual(c[:2], ['busctl', 'call'])

    def test_sans_privileges(self):
        c = sy.commande_bridage(42, self.CG_USER, False, 256, 'u.scope')
        self.assertEqual(c[:3], ['busctl', '--user', 'call'])

    def test_parametres_du_scope(self):
        c = sy.commande_bridage(4242, self.CG_SYS, True, 128, 'gagarine-fige-4242-1.scope')
        i = c.index('ssa(sv)a(sa(sv))')
        self.assertEqual(c[i + 1:i + 3], ['gagarine-fige-4242-1.scope', 'fail'])
        self.assertEqual(c[c.index('PIDs'):c.index('PIDs') + 4], ['PIDs', 'au', '1', '4242'])
        self.assertEqual(c[c.index('MemoryMax') + 2], str(128 * Mo))
        self.assertEqual(c[c.index('MemorySwapMax') + 1:c.index('MemorySwapMax') + 3], ['t', '0'])

    SORTIE = {
        ('loginctl', 'list-sessions', '--no-legend'): '  2 1000 charles seat0 tty2\n  c1 120 gdm seat0 tty1\n  4 1001 bob - pts/0\n'
                                                      '  5 1000 charles seat0 tty3\n',
        ('loginctl', 'show-session', '2', '-p', 'Type', '-p', 'Active'): 'Type=wayland\nActive=yes\n',
        ('loginctl', 'show-session', '4', '-p', 'Type', '-p', 'Active'): 'Type=tty\nActive=yes\n',
        ('loginctl', 'show-session', '5', '-p', 'Type', '-p', 'Active'): 'Type=x11\nActive=no\n',
    }

    def test_sessions_graphiques_actives(self):
        self.assertEqual(sy.sessions_graphiques(lambda c: self.SORTIE.get(tuple(c), '')), [(1000, 'charles')])

    def test_notification_root_par_session(self):
        cmds = sy.commandes_notification('T', 'X', True, True, [(1000, 'charles'), (1001, 'bob')])
        self.assertEqual(len(cmds), 2)
        self.assertEqual(cmds[0][:4], ['runuser', '-u', 'charles', '--'])
        self.assertIn('DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus', cmds[0])
        self.assertIn('arreter=Arrêter', cmds[0])
        self.assertIn('reprendre=Reprendre', cmds[0])
        self.assertEqual(cmds[0][-2:], ['T', 'X'])

    def test_notification_sans_privileges_directe_et_sans_boutons(self):
        cmds = sy.commandes_notification('T', 'X', False, False, [])
        self.assertEqual(cmds[0][0], 'notify-send')
        self.assertNotIn('-A', cmds[0])

    def test_reponse_ignoree_si_le_pid_a_change(self):
        s = sy.Systeme(rg.Reglages(), root=False)
        signaux = []
        s.tuer = lambda pid, sig: signaux.append((pid, sig))
        vieux = sy.subprocess.run

        class R:
            stdout = 'arreter\n'
        sy.subprocess.run = lambda *a, **k: R()
        try:
            s._attendre_choix(['x'], os.getpid(), 'pas-le-bon-debut')
            self.assertEqual(signaux, [])
            s._attendre_choix(['x'], os.getpid(), sy.demarrage(os.getpid()))
            self.assertEqual(signaux, [(os.getpid(), signal.SIGKILL)])
        finally:
            sy.subprocess.run = vieux

    def test_etat_service(self):
        appels = []

        def lancer(c):
            appels.append(c)
            return 'active\n'
        self.assertEqual(cli.etat_service(lancer, root=True), [('gagarine', 'active')])
        self.assertEqual([c[-1] for c in appels], ['gagarine'])
        self.assertEqual(len(cli.etat_service(lancer, root=False)), 2)
        self.assertIn(['systemctl', '--user', 'is-active', 'garde-ram'], appels)
