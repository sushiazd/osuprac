// Calculateur de pp osu! standard utilisant le code officiel d'osu! (ppy/osu), donc les mêmes valeurs
// que le classement « Live » de pp.huismetbenen.nl.
//
// Protocole : une requête JSON par ligne sur l'entrée standard, une réponse JSON par ligne sur la sortie.
//   {"id": 1, "path": "C:/.../123.osu", "mods": [{"acronym": "DT"}],
//    "scores": [{"acc": 0.9, "miss": 0}, {"n300": 300, "n100": 20, "n50": 1, "miss": 2, "combo": 250,
//               "slider_tail_hit": 80, "large_tick_miss": 1}]}
// Sans "scores", seule la difficulté est renvoyée.
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using osu.Framework.Audio.Track;
using osu.Framework.Graphics.Textures;
using osu.Game.Beatmaps;
using osu.Game.Beatmaps.Formats;
using osu.Game.IO;
using osu.Game.Online.API;
using osu.Game.Rulesets;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Difficulty;
using osu.Game.Rulesets.Osu.Mods;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Rulesets.Scoring;
using osu.Game.Scoring;
using osu.Game.Skinning;

internal static class Program
{
    private static readonly OsuRuleset ruleset = new OsuRuleset();

    private static void Main(string[] args)
    {
        Console.InputEncoding = System.Text.Encoding.UTF8;
        var stdout = new StreamWriter(Console.OpenStandardOutput(), new System.Text.UTF8Encoding(false)) { AutoFlush = true };

        if (args.Length > 0 && args[0] == "--version")
        {
            stdout.WriteLine(JsonConvert.SerializeObject(new { engine = "osu-live", commit = Commit }));
            return;
        }

        string line;
        while ((line = Console.ReadLine()) != null)
        {
            if (string.IsNullOrWhiteSpace(line))
                continue;

            JToken id = null;
            string response;
            try
            {
                var req = JObject.Parse(line);
                id = req["id"];
                response = JsonConvert.SerializeObject(Handle(req, id));
            }
            catch (Exception e)
            {
                response = JsonConvert.SerializeObject(new { id, ok = false, error = e.Message });
            }

            stdout.WriteLine(response);
        }
    }

    public const string Commit = "b11b274d1cb5c22eabe9dba5df14fa1e4ecc4e6d";

    private static object Handle(JObject req, JToken id)
    {
        if ((string)req["cmd"] == "version")
            return new { id, ok = true, engine = "osu-live", commit = Commit };

        string path = (string)req["path"] ?? throw new ArgumentException("path manquant");
        var apiMods = req["mods"]?.ToObject<List<APIMod>>() ?? new List<APIMod>();
        var mods = apiMods.Select(m => m.ToMod(ruleset)).ToArray();

        var working = new FileWorkingBeatmap(path);
        var beatmap = working.GetPlayableBeatmap(ruleset.RulesetInfo, mods);
        var diff = (OsuDifficultyAttributes)ruleset.CreateDifficultyCalculator(working).Calculate(mods);

        var scores = new List<object>();
        var perfCalc = ruleset.CreatePerformanceCalculator()!;
        bool classic = mods.OfType<OsuModClassic>().Any(m => m.NoSliderHeadAccuracy.Value);

        foreach (var s in (JArray)req["scores"] ?? new JArray())
        {
            var stats = Statistics(beatmap, (JObject)s, classic);
            var score = new ScoreInfo(beatmap.BeatmapInfo, ruleset.RulesetInfo)
            {
                Accuracy = Accuracy(beatmap, stats),
                MaxCombo = (int?)s["combo"] ?? diff.MaxCombo,
                Statistics = stats,
                Mods = mods,
            };
            var p = (OsuPerformanceAttributes)perfCalc.Calculate(score, diff);
            scores.Add(new
            {
                pp = p.Total, aim = p.Aim, speed = p.Speed, accuracy = p.Accuracy, flashlight = p.Flashlight, reading = p.Reading,
                acc = score.Accuracy, combo = score.MaxCombo, effective_miss = p.EffectiveMissCount,
            });
        }

        var d = beatmap.Difficulty;
        return new
        {
            id, ok = true,
            difficulty = new
            {
                stars = diff.StarRating, aim = diff.AimDifficulty, speed = diff.SpeedDifficulty, reading = diff.ReadingDifficulty,
                flashlight = diff.FlashlightDifficulty, slider_factor = diff.SliderFactor, speed_note_count = diff.SpeedNoteCount,
                max_combo = diff.MaxCombo, circles = diff.HitCircleCount, sliders = diff.SliderCount, spinners = diff.SpinnerCount,
                cs = d.CircleSize, ar = d.ApproachRate, od = d.OverallDifficulty, hp = d.DrainRate,
            },
            scores,
        };
    }

    /// <summary>Statistiques d'une partie : réelles si fournies, sinon déduites de la précision comme le fait osu-tools.</summary>
    private static Dictionary<HitResult, int> Statistics(IBeatmap beatmap, JObject s, bool classic)
    {
        int total = beatmap.HitObjects.Count;
        int sliders = beatmap.HitObjects.Count(o => o is Slider);
        int miss = (int?)s["miss"] ?? 0;
        int great, ok, meh;

        if (s["n300"] != null || s["n100"] != null || s["n50"] != null)
        {
            ok = (int?)s["n100"] ?? 0;
            meh = (int?)s["n50"] ?? 0;
            great = (int?)s["n300"] ?? total - ok - meh - miss;
        }
        else
        {
            double accuracy = (double?)s["acc"] ?? 1.0;
            int relevant = total - miss;
            double relAcc = Math.Clamp(accuracy * total / Math.Max(1, relevant), 0, 1);

            if (relAcc >= 0.25)
            {
                double ratio50To100 = Math.Pow(1 - (relAcc - 0.25) / 0.75, 2);
                double c100 = 6 * relevant * (1 - relAcc) / (5 * ratio50To100 + 4);
                double c50 = c100 * ratio50To100;
                ok = (int)Math.Round(c100);
                meh = (int)(Math.Round(c100 + c50) - ok);
            }
            else if (relAcc >= 1.0 / 6)
            {
                double c100 = 6 * relevant * relAcc - relevant;
                ok = (int)Math.Round(c100);
                meh = relevant - ok;
            }
            else
            {
                ok = 0;
                meh = (int)Math.Round(6 * relevant * relAcc);
                miss = total - meh;
            }

            great = total - ok - meh - miss;
        }

        var stats = new Dictionary<HitResult, int>
        {
            { HitResult.Great, great }, { HitResult.Ok, ok }, { HitResult.Meh, meh }, { HitResult.Miss, miss },
        };

        if (!classic)
        {
            stats[HitResult.LargeTickMiss] = (int?)s["large_tick_miss"] ?? 0;
            stats[HitResult.SliderTailHit] = (int?)s["slider_tail_hit"] ?? sliders;
        }

        return stats;
    }

    private static double Accuracy(IBeatmap beatmap, Dictionary<HitResult, int> stats)
    {
        double total = 6 * stats[HitResult.Great] + 2 * stats[HitResult.Ok] + stats[HitResult.Meh];
        double max = 6 * (stats[HitResult.Great] + stats[HitResult.Ok] + stats[HitResult.Meh] + stats[HitResult.Miss]);

        if (stats.TryGetValue(HitResult.SliderTailHit, out int tailHit))
        {
            total += 3 * tailHit;
            max += 3 * beatmap.HitObjects.Count(o => o is Slider);
        }

        if (stats.TryGetValue(HitResult.LargeTickMiss, out int tickMiss))
        {
            int ticks = beatmap.HitObjects.Sum(o => o.NestedHitObjects.Count(x => x is SliderTick or SliderRepeat));
            total += 0.6 * (ticks - tickMiss);
            max += 0.6 * ticks;
        }

        return max > 0 ? total / max : 1;
    }

    private class FileWorkingBeatmap : WorkingBeatmap
    {
        private readonly Beatmap beatmap;

        public FileWorkingBeatmap(string file)
            : this(Read(file))
        {
        }

        private FileWorkingBeatmap(Beatmap beatmap)
            : base(beatmap.BeatmapInfo, null)
        {
            this.beatmap = beatmap;
            beatmap.BeatmapInfo.Ruleset = ruleset.RulesetInfo;
        }

        private static Beatmap Read(string file)
        {
            using var stream = File.OpenRead(file);
            using var reader = new LineBufferedReader(stream);
            return Decoder.GetDecoder<Beatmap>(reader).Decode(reader);
        }

        protected override IBeatmap GetBeatmap() => beatmap;
        public override Texture GetBackground() => null;
        protected override Track GetBeatmapTrack() => null;
        protected override ISkin GetSkin() => null;
        public override Stream GetStream(string storagePath) => null;
    }
}
