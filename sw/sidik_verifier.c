/* SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * SIDIK verifier for Linux on the DE10-Nano HPS: the C equivalent of
 * sw/verifier.py (same flows, same database file, same exit codes).
 *
 *   sidik_verifier [transport] enroll     [-i A|B] -d DB [-n 16] [-t 64]
 *   sidik_verifier [transport] auth       [-i A|B] -d DB
 *   sidik_verifier [transport] clone-demo [-n 4] [-t 64] [-D DIR]
 *
 * Transports:
 *   -m [-b BRIDGE] [-w WINDOW]  /dev/mem, lightweight bridge 0xFF200000,
 *                               SIDIK window at +0x40000 (root only)
 *   -j HOST:PORT                fpga/release/syscon/sidik_bridge.tcl (System
 *                               Console, JTAG to Avalon master), host PC
 *   -s PATH                     sw/sidik_sim.py --socket PATH
 *
 * Exit codes: 0 accepted / done, 1 rejected, 2 error.
 * Build: gcc -O2 -Wall -Wextra -o sidik_verifier sidik_verifier.c
 * No crypto library: the verifier compares stored responses only.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "sidik_regs.h"

#define LW_BRIDGE_BASE  0xFF200000ul
#define DEFAULT_WINDOW  0x40000ul
#define DEFAULT_TAU     64u
#define MAX_POLLS       100000
#define MAX_CRPS        4096
#define DB_HEADER       "# sidik-verifier db v1"

/* ---- transports ---------------------------------------------------------------- */

struct bus {
    volatile uint32_t *mem;   /* /dev/mem mapping of the window, or NULL */
    FILE *sock;               /* sidik_sim.py socket, or NULL */
};

static void die(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fputs("error: ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
    exit(2);
}

static int bus_open_mem(struct bus *b, unsigned long bridge, unsigned long window)
{
    long pg = sysconf(_SC_PAGESIZE);
    unsigned long phys = bridge + window, page = phys & ~(unsigned long)(pg - 1);
    int fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0)
        return -1;
    void *p = mmap(NULL, (phys - page) + 0x200, PROT_READ | PROT_WRITE, MAP_SHARED,
                   fd, (off_t)page);
    close(fd);
    if (p == MAP_FAILED)
        return -1;
    b->mem = (volatile uint32_t *)((char *)p + (phys - page));
    b->sock = NULL;
    return 0;
}

static int bus_open_socket(struct bus *b, const char *path)
{
    struct sockaddr_un sa = { .sun_family = AF_UNIX };
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0 || strlen(path) >= sizeof sa.sun_path)
        return -1;
    strcpy(sa.sun_path, path);
    if (connect(fd, (struct sockaddr *)&sa, sizeof sa) < 0) {
        close(fd);
        return -1;
    }
    b->sock = fdopen(fd, "r+");
    b->mem = NULL;
    return b->sock ? 0 : -1;
}

static int bus_open_tcp(struct bus *b, const char *host_port)
{
    char host[256];
    const char *colon = strrchr(host_port, ':');
    struct addrinfo hints = { .ai_family = AF_UNSPEC, .ai_socktype = SOCK_STREAM }, *res, *r;
    int fd = -1;
    if (!colon || (size_t)(colon - host_port) >= sizeof host)
        return -1;
    memcpy(host, host_port, colon - host_port);
    host[colon - host_port] = 0;
    if (getaddrinfo(host[0] ? host : "127.0.0.1", colon + 1, &hints, &res))
        return -1;
    for (r = res; r; r = r->ai_next) {
        fd = socket(r->ai_family, r->ai_socktype, r->ai_protocol);
        if (fd >= 0 && connect(fd, r->ai_addr, r->ai_addrlen) == 0)
            break;
        if (fd >= 0)
            close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0)
        return -1;
    b->sock = fdopen(fd, "r+");
    b->mem = NULL;
    return b->sock ? 0 : -1;
}

static uint32_t sock_req(struct bus *b, const char *line)
{
    char reply[64];
    fputs(line, b->sock);
    fflush(b->sock);
    if (!fgets(reply, sizeof reply, b->sock) || !strncmp(reply, "ERR", 3))
        die("simulator rejected %s", line);
    return (uint32_t)strtoul(reply, NULL, 16);
}

static uint32_t rd(struct bus *b, uint32_t off)
{
    char line[32];
    if (b->mem)
        return b->mem[off / 4];
    snprintf(line, sizeof line, "R %x\n", off);
    return sock_req(b, line);
}

static void wr(struct bus *b, uint32_t off, uint32_t v)
{
    char line[48];
    if (b->mem) {
        b->mem[off / 4] = v;
        return;
    }
    snprintf(line, sizeof line, "W %x %x\n", off, v);
    sock_req(b, line);
}

static void bus_pause(struct bus *b)
{
    if (b->mem) {
        struct timespec ts = { 0, 1000000 };
        nanosleep(&ts, NULL);
    }
}

/* ---- one SIDIK instance -------------------------------------------------------- */

struct sidik {
    struct bus *bus;
    uint32_t base;
};

static uint32_t s_rd(struct sidik *d, uint32_t off) { return rd(d->bus, d->base + off); }
static void s_wr(struct sidik *d, uint32_t off, uint32_t v) { wr(d->bus, d->base + off, v); }

static void rd_bytes(struct sidik *d, uint32_t off, uint8_t out[32])
{
    for (int i = 0; i < 8; i++) {
        uint32_t w = s_rd(d, off + 4 * i);
        out[4 * i] = w >> 24; out[4 * i + 1] = w >> 16;
        out[4 * i + 2] = w >> 8; out[4 * i + 3] = w;
    }
}

static void wr_bytes(struct sidik *d, uint32_t off, const uint8_t in[32])
{
    for (int i = 0; i < 8; i++)
        s_wr(d, off + 4 * i, (uint32_t)in[4 * i] << 24 | (uint32_t)in[4 * i + 1] << 16 |
                             (uint32_t)in[4 * i + 2] << 8 | in[4 * i + 3]);
}

static void check_magic(struct sidik *d)
{
    uint32_t m = s_rd(d, SIDIK_MAGIC);
    if (m != SIDIK_MAGIC_VALUE)
        die("no SIDIK at offset 0x%x (MAGIC 0x%08x)", d->base, m);
}

/* Write CTRL, wait for DONE (or TAMPERED); returns STATUS. */
static uint32_t command(struct sidik *d, uint32_t bit)
{
    s_wr(d, SIDIK_CTRL, bit);
    for (int i = 0; i < MAX_POLLS; i++) {
        uint32_t s = s_rd(d, SIDIK_STATUS);
        if ((s & SIDIK_TAMPERED) || (!(s & SIDIK_BUSY) && (s & SIDIK_DONE)))
            return s;
        bus_pause(d->bus);
    }
    die("timeout waiting for the device");
    return 0;
}

static void clear(struct sidik *d) { s_wr(d, SIDIK_CTRL, SIDIK_CLEAR); }

/* AUTH on `chal`; returns 0 and fills `resp`, or -1 with *status set. */
static int auth(struct sidik *d, const uint8_t chal[32], uint8_t resp[32], uint32_t *status)
{
    wr_bytes(d, SIDIK_CHAL, chal);
    *status = command(d, SIDIK_AUTH);
    if (*status & (SIDIK_ERR | SIDIK_TAMPERED))
        return -1;
    rd_bytes(d, SIDIK_RESP, resp);
    return 0;
}

/* ---- database ------------------------------------------------------------------ */

struct crp {
    uint8_t chal[32], resp[32];
    int used;
};

struct db {
    uint8_t id[32];
    uint32_t helper[SIDIK_N_HELPER_WORDS];
    int n_crp;
    struct crp crp[MAX_CRPS];
};

static void hex_out(FILE *f, const uint8_t *b, int n)
{
    for (int i = 0; i < n; i++)
        fprintf(f, "%02x", b[i]);
}

static int hex_in(const char *s, uint8_t *b, int n)
{
    if ((int)strlen(s) != 2 * n)
        return -1;
    for (int i = 0; i < n; i++) {
        unsigned v;
        if (sscanf(s + 2 * i, "%2x", &v) != 1)
            return -1;
        b[i] = (uint8_t)v;
    }
    return 0;
}

/* Atomic replace (write, fsync, rename): a used CRP is never lost. */
static void db_save(const struct db *db, const char *path)
{
    char tmp[4096];
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = fopen(tmp, "w");
    if (!f)
        die("cannot write %s: %s", tmp, strerror(errno));
    fprintf(f, "%s\nid ", DB_HEADER);
    hex_out(f, db->id, 32);
    fputs("\nhelper", f);
    for (int i = 0; i < SIDIK_N_HELPER_WORDS; i++)
        fprintf(f, " %08x", db->helper[i]);
    fputc('\n', f);
    for (int i = 0; i < db->n_crp; i++) {
        fputs("crp ", f);
        hex_out(f, db->crp[i].chal, 32);
        fputc(' ', f);
        hex_out(f, db->crp[i].resp, 32);
        fprintf(f, " %d\n", db->crp[i].used);
    }
    if (fflush(f) || fsync(fileno(f)) || fclose(f) || rename(tmp, path))
        die("cannot save %s: %s", path, strerror(errno));
}

static void db_load(struct db *db, const char *path)
{
    char line[512];
    int have_id = 0, have_helper = 0;
    FILE *f = fopen(path, "r");
    if (!f)
        die("cannot read %s: %s", path, strerror(errno));
    memset(db, 0, sizeof *db);
    if (!fgets(line, sizeof line, f) || strncmp(line, DB_HEADER, strlen(DB_HEADER)))
        die("%s: not a sidik-verifier v1 database", path);
    while (fgets(line, sizeof line, f)) {
        char *tok[SIDIK_N_HELPER_WORDS + 2], *save = NULL;
        int n = 0;
        for (char *t = strtok_r(line, " \t\r\n", &save); t && n < SIDIK_N_HELPER_WORDS + 2;
             t = strtok_r(NULL, " \t\r\n", &save))
            tok[n++] = t;
        if (n == 0 || tok[0][0] == '#')
            continue;
        if (!strcmp(tok[0], "id") && n == 2 && !hex_in(tok[1], db->id, 32)) {
            have_id = 1;
        } else if (!strcmp(tok[0], "helper") && n == SIDIK_N_HELPER_WORDS + 1) {
            for (int i = 0; i < SIDIK_N_HELPER_WORDS; i++) {
                char *end;
                db->helper[i] = (uint32_t)strtoul(tok[i + 1], &end, 16);
                if (*end || strlen(tok[i + 1]) != 8)
                    die("%s: bad helper word", path);
            }
            have_helper = 1;
        } else if (!strcmp(tok[0], "crp") && n == 4 && db->n_crp < MAX_CRPS &&
                   !hex_in(tok[1], db->crp[db->n_crp].chal, 32) &&
                   !hex_in(tok[2], db->crp[db->n_crp].resp, 32) &&
                   (!strcmp(tok[3], "0") || !strcmp(tok[3], "1"))) {
            db->crp[db->n_crp++].used = tok[3][0] == '1';
        } else {
            die("%s: bad database line starting '%s'", path, tok[0]);
        }
    }
    fclose(f);
    if (!have_id || !have_helper)
        die("%s: database lacks id or helper", path);
}

/* ---- flows --------------------------------------------------------------------- */

static int ct_equal(const uint8_t *a, const uint8_t *b, int n)
{
    uint8_t x = 0;
    for (int i = 0; i < n; i++)
        x |= a[i] ^ b[i];
    return x == 0;
}

static void enroll_device(struct sidik *d, int n_crp, uint32_t tau, struct db *db)
{
    uint32_t s;
    if (n_crp < 0 || n_crp > MAX_CRPS)
        die("-n must be 0..%d", MAX_CRPS);
    check_magic(d);
    memset(db, 0, sizeof *db);
    s_wr(d, SIDIK_TAU, tau);
    s = command(d, SIDIK_ENROLL);
    if ((s & (SIDIK_ERR | SIDIK_TAMPERED)) || !(s & SIDIK_K_READY)) {
        clear(d);
        die("enrollment failed (STATUS 0x%02x)", s);
    }
    for (int i = 0; i < SIDIK_N_HELPER_WORDS; i++)
        db->helper[i] = s_rd(d, SIDIK_HELPER + 4 * i);
    rd_bytes(d, SIDIK_ID, db->id);
    for (int i = 0; i < n_crp; i++) {
        struct crp *c = &db->crp[i];
        if (getrandom(c->chal, 32, 0) != 32) {
            clear(d);
            die("getrandom: %s", strerror(errno));
        }
        if (auth(d, c->chal, c->resp, &s)) {
            clear(d);
            die("AUTH failed during enrollment (STATUS 0x%02x)", s);
        }
        db->n_crp++;
    }
    clear(d);
}

static int unused(const struct db *db)
{
    int n = 0;
    for (int i = 0; i < db->n_crp; i++)
        n += !db->crp[i].used;
    return n;
}

/* One session; returns 1 accepted, 0 rejected, with the reason in `why`.
 * `db_path` (or NULL) receives the database after the CRP is burnt. */
static int authenticate(struct sidik *d, struct db *db, const char *db_path,
                        char *why, size_t len)
{
    uint8_t id[32], resp[32];
    struct crp *c = NULL;
    uint32_t s;
    int ok = 0;

    check_magic(d);
    for (int i = 0; i < SIDIK_N_HELPER_WORDS; i++)
        s_wr(d, SIDIK_HELPER + 4 * i, db->helper[i]);
    s = command(d, SIDIK_RECONSTRUCT);
    if (s & SIDIK_TAMPERED) {
        snprintf(why, len, "device tampered");
        goto out;
    }
    if ((s & (SIDIK_ERR | SIDIK_RECON_FAIL)) || !(s & SIDIK_K_READY)) {
        snprintf(why, len, "%s (STATUS 0x%02x)",
                 s & SIDIK_RECON_FAIL ? "reconstruction failed" : "device error", s);
        goto out;
    }
    rd_bytes(d, SIDIK_ID, id);
    if (!ct_equal(id, db->id, 32)) {
        snprintf(why, len, "ID mismatch");
        goto out;
    }
    for (int i = 0; i < db->n_crp && !c; i++)
        if (!db->crp[i].used)
            c = &db->crp[i];
    if (!c) {
        snprintf(why, len, "no unused challenge left: re-enroll");
        goto out;
    }
    c->used = 1;
    if (db_path)
        db_save(db, db_path);   /* burn the challenge before the device sees it */
    if (auth(d, c->chal, resp, &s)) {
        snprintf(why, len, "AUTH failed (STATUS 0x%02x)", s);
        goto out;
    }
    if (!ct_equal(resp, c->resp, 32)) {
        snprintf(why, len, "response mismatch");
        goto out;
    }
    snprintf(why, len, "accepted (%d challenges left)", unused(db));
    ok = 1;
out:
    clear(d);
    return ok;
}

/* ---- command line -------------------------------------------------------------- */

static void usage(void)
{
    fputs("usage: sidik_verifier (-m [-b BRIDGE] [-w WINDOW] | -j HOST:PORT | -s SOCKET)\n"
          "         enroll [-i A|B] -d DB [-n 16] [-t 64]\n"
          "       | auth [-i A|B] -d DB\n"
          "       | clone-demo [-n 4] [-t 64] [-D DIR]\n", stderr);
    exit(2);
}

static struct db g_db;   /* large: keep off the stack */

int main(int argc, char **argv)
{
    struct bus bus = { 0 };
    unsigned long bridge = LW_BRIDGE_BASE, window = DEFAULT_WINDOW;
    const char *sock = NULL, *jtag = NULL, *db_path = NULL, *dir = NULL;
    int use_mem = 0, n = -1, opt;
    uint32_t tau = DEFAULT_TAU, base = SIDIK_INSTANCE_A;
    char why[128];

    while ((opt = getopt(argc, argv, "+mb:w:j:s:")) != -1) {
        switch (opt) {
        case 'm': use_mem = 1; break;
        case 'b': bridge = strtoul(optarg, NULL, 0); break;
        case 'w': window = strtoul(optarg, NULL, 0); break;
        case 'j': jtag = optarg; break;
        case 's': sock = optarg; break;
        default: usage();
        }
    }
    if (optind >= argc || use_mem + !!sock + !!jtag != 1)
        usage();
    const char *cmd = argv[optind];
    int sub_argc = argc - optind;          /* sub-command options: argv[0] = cmd */
    char **sub_argv = argv + optind;
    optind = 0;                            /* restart getopt */
    while ((opt = getopt(sub_argc, sub_argv, "+i:d:n:t:D:")) != -1) {
        switch (opt) {
        case 'i':
            if (!strcmp(optarg, "A")) base = SIDIK_INSTANCE_A;
            else if (!strcmp(optarg, "B")) base = SIDIK_INSTANCE_B;
            else usage();
            break;
        case 'd': db_path = optarg; break;
        case 'n': n = atoi(optarg); break;
        case 't': tau = (uint32_t)strtoul(optarg, NULL, 0); break;
        case 'D': dir = optarg; break;
        default: usage();
        }
    }
    if (optind != sub_argc)
        usage();

    int err = jtag ? bus_open_tcp(&bus, jtag)
            : sock ? bus_open_socket(&bus, sock) : bus_open_mem(&bus, bridge, window);
    if (err)
        die("cannot open %s: %s", jtag ? jtag : sock ? sock : "/dev/mem", strerror(errno));

    struct sidik dev = { &bus, base };
    if (!strcmp(cmd, "enroll")) {
        if (!db_path)
            usage();
        enroll_device(&dev, n < 0 ? 16 : n, tau, &g_db);
        db_save(&g_db, db_path);
        printf("enrolled %c: ID ", base == SIDIK_INSTANCE_A ? 'A' : 'B');
        hex_out(stdout, g_db.id, 32);
        printf(", %d CRPs -> %s\n", g_db.n_crp, db_path);
        return 0;
    }
    if (!strcmp(cmd, "auth")) {
        if (!db_path)
            usage();
        db_load(&g_db, db_path);
        int ok = authenticate(&dev, &g_db, db_path, why, sizeof why);
        printf("%s: %s\n", ok ? "ACCEPT" : "REJECT", why);
        return ok ? 0 : 1;
    }
    if (!strcmp(cmd, "clone-demo")) {
        char path[4096];
        struct sidik a = { &bus, SIDIK_INSTANCE_A }, b = { &bus, SIDIK_INSTANCE_B };
        if (dir)
            snprintf(path, sizeof path, "%s/a.db", dir);
        enroll_device(&a, n < 0 ? 4 : n, tau, &g_db);
        printf("enrolled A: ID ");
        hex_out(stdout, g_db.id, 8);
        printf("..., %d CRPs\n", g_db.n_crp);
        if (dir)
            db_save(&g_db, path);
        int ok_a = authenticate(&a, &g_db, dir ? path : NULL, why, sizeof why);
        printf("A with A's helper data: %s - %s\n", ok_a ? "ACCEPT" : "REJECT", why);
        int ok_b = authenticate(&b, &g_db, dir ? path : NULL, why, sizeof why);
        printf("B with A's helper data: %s - %s\n", ok_b ? "ACCEPT" : "REJECT", why);
        int ok = ok_a && !ok_b;
        printf("clone demo: %s\n", ok ? "B rejected, A accepted" : "UNEXPECTED");
        return ok ? 0 : 1;
    }
    usage();
    return 2;
}
