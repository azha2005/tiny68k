/* harness.c -- the original TINY.C, unmodified, driven from the host.

   TINY.C is included as-is (its own main renamed), so this binary IS the
   floating-point original. It is the reference that ref_tiny.py must match
   and that the integer port is measured against.

     harness MODEL logits TOK TOK ...   print VOCAB scores per position
     harness MODEL answer < QUESTIONS   greedy answers (creativity 0), one per line

   Build: gcc -O2 -o harness ref/harness.c -lm      (see build.py ref)   */

#define main tiny_original_main
#include "../orig/TINY.C"
#undef main

int main(int argc, char **argv)
{
    int i, p;
    if (argc < 3) {
        fprintf(stderr, "usage: harness MODEL logits TOK... | answer\n");
        return 2;
    }
    load_model(argv[1]);
    alloc_memory();

    if (strcmp(argv[2], "logits") == 0) {
        for (p = 0; p + 3 < argc; p++) {
            step(atoi(argv[p + 3]), p, score);
            for (i = 0; i < VOCAB; i++)
                printf("%.9g\n", score[i]);
        }
        return 0;
    }
    if (strcmp(argv[2], "answer") == 0) {
        static char line[256], q[MAXQ + 1], out[CTX + 1];
        long tenths;
        creativity = 0;
        while (fgets(line, sizeof(line), stdin)) {
            if (clean(line, q) == 0)
                continue;
            think(q, out, &tenths, NULL, NULL);
            printf("%s\n", out);
        }
        return 0;
    }
    fprintf(stderr, "unknown mode %s\n", argv[2]);
    return 2;
}
