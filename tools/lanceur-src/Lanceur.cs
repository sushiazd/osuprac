// « osu progression.exe » : ouvre le tableau de bord dans sa propre fenêtre, sans terminal ni navigateur.
// Tout est déjà installé : lance tools\fenetre.pyw avec pythonw.exe (aucune console) et s'arrête aussitôt.
// Première fois (ou dépendances à compléter) : passe la main à lancer.bat, dont la console montre l'installation.
// Reconstruire : powershell -ExecutionPolicy Bypass -File tools\lanceur-src\build.ps1
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Lanceur
{
    [STAThread]
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string pythonw = Path.Combine(root, @".venv\Scripts\pythonw.exe");
        string packages = Path.Combine(root, @".venv\Lib\site-packages");
        bool ready = File.Exists(pythonw)
            && Directory.Exists(Path.Combine(packages, "webview"))
            && Directory.Exists(Path.Combine(packages, "streamlit"))
            && File.Exists(Path.Combine(root, ".env"));
        try
        {
            var start = ready
                ? new ProcessStartInfo(pythonw, "\"" + Path.Combine(root, @"tools\fenetre.pyw") + "\"")
                : new ProcessStartInfo(Path.Combine(root, "lancer.bat"));
            start.WorkingDirectory = root;
            start.UseShellExecute = !ready;
            Process.Start(start);
        }
        catch (Exception e)
        {
            MessageBox.Show("osu! progression n'a pas pu se lancer :\n\n" + e.Message, "osu! progression",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
