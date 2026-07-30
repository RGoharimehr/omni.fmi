/* SPDX-FileCopyrightText: 2026 Reza Goharimehr <rgoharim@villanova.edu>
 * SPDX-License-Identifier: Apache-2.0
 *
 * PipeHeatLoad - FMI 2.0 Co-Simulation FMU
 *
 * A heated pipe discretized into N_SEG segments, with sensible heating and
 * evaporation (boiling). Stands in for a tool-exported flow-network FMU
 * (e.g. Flownex) so the USD/FMI pipeline can be exercised end to end.
 *
 * Physics per step:
 *   - the total heat load is split evenly over the segments
 *   - marching downstream, each segment raises the fluid temperature by
 *       dT = q_seg / (m_dot * cp)
 *     until it reaches T_sat; any remaining energy goes into vaporization,
 *       dx = q_lat / (m_dot * h_fg)
 *   - segment states relax toward that target with a first-order lag (tau),
 *     so transients are visible rather than instantaneous
 *   - pressure drop uses Darcy-Weisbach with a simple two-phase multiplier
 *
 * Self-contained: the minimal FMI 2.0 declarations are inlined, so no external
 * FMI headers are needed to build.
 */
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* ------------------------------------------------------------------ FMI 2.0 */
typedef void*        fmi2Component;
typedef void*        fmi2ComponentEnvironment;
typedef void*        fmi2FMUstate;
typedef unsigned int fmi2ValueReference;
typedef double       fmi2Real;
typedef int          fmi2Integer;
typedef int          fmi2Boolean;
typedef char         fmi2Char;
typedef const fmi2Char* fmi2String;
typedef char         fmi2Byte;

#define fmi2True  1
#define fmi2False 0

typedef enum { fmi2OK, fmi2Warning, fmi2Discard, fmi2Error, fmi2Fatal, fmi2Pending } fmi2Status;
typedef enum { fmi2ModelExchange, fmi2CoSimulation } fmi2Type;
typedef enum { fmi2DoStepStatus, fmi2PendingStatus, fmi2LastSuccessfulTime, fmi2Terminated } fmi2StatusKind;

typedef void  (*fmi2CallbackLogger)(fmi2ComponentEnvironment, fmi2String, fmi2Status, fmi2String, fmi2String, ...);
typedef void* (*fmi2CallbackAllocateMemory)(size_t, size_t);
typedef void  (*fmi2CallbackFreeMemory)(void*);
typedef void  (*fmi2StepFinished)(fmi2ComponentEnvironment, fmi2Status);

typedef struct {
    fmi2CallbackLogger         logger;
    fmi2CallbackAllocateMemory allocateMemory;
    fmi2CallbackFreeMemory     freeMemory;
    fmi2StepFinished           stepFinished;
    fmi2ComponentEnvironment   componentEnvironment;
} fmi2CallbackFunctions;

#define FMI2_EXPORT __declspec(dllexport)

/* ------------------------------------------------------------------- model */
#define N_SEG 8

/* value references */
#define VR_M_DOT    0
#define VR_T_IN     1
#define VR_Q_TOTAL  2
#define VR_P_IN     3

#define VR_L        10
#define VR_D        11
#define VR_CP       12
#define VR_H_FG     13
#define VR_T_SAT    14
#define VR_RHO      15
#define VR_TAU      16

#define VR_T_BASE   20   /* T_1 .. T_8  -> 20..27 */
#define VR_X_BASE   30   /* x_1 .. x_8  -> 30..37 */
#define VR_T_OUT    40
#define VR_X_OUT    41
#define VR_DP_TOTAL 42
#define VR_Q_ABS    43

typedef struct {
    /* inputs */
    double m_dot, T_in, Q_total, p_in;
    /* parameters */
    double L, D, cp, h_fg, T_sat, rho, tau;
    /* states / outputs */
    double T[N_SEG], x[N_SEG];
    double T_out, x_out, dp_total, Q_abs;
    double time;
    int initialized;
} PipeModel;

static void set_defaults(PipeModel* m) {
    m->m_dot   = 0.05;      /* kg/s  */
    m->T_in    = 25.0;      /* degC  */
    m->Q_total = 5000.0;    /* W     */
    m->p_in    = 200.0;     /* kPa   */

    m->L     = 2.0;         /* m           */
    m->D     = 0.012;       /* m           */
    m->cp    = 4180.0;      /* J/(kg K)    */
    m->h_fg  = 2.26e6;      /* J/kg        */
    m->T_sat = 60.0;        /* degC        */
    m->rho   = 997.0;       /* kg/m3       */
    m->tau   = 2.0;         /* s           */

    for (int i = 0; i < N_SEG; i++) { m->T[i] = m->T_in; m->x[i] = 0.0; }
    m->T_out = m->T_in;
    m->x_out = 0.0;
    m->dp_total = 0.0;
    m->Q_abs = 0.0;
    m->time = 0.0;
    m->initialized = 0;
}

/* Steady-state profile for the current inputs, then first-order relaxation. */
static void advance(PipeModel* m, double dt) {
    double m_dot = m->m_dot < 1e-5 ? 1e-5 : m->m_dot;   /* guard divide-by-zero */
    double cp    = m->cp   < 1.0  ? 1.0  : m->cp;
    double h_fg  = m->h_fg < 1.0  ? 1.0  : m->h_fg;
    double q_seg = m->Q_total / (double)N_SEG;

    double T_target[N_SEG], x_target[N_SEG];
    double T_prev = m->T_in, x_prev = 0.0;

    for (int i = 0; i < N_SEG; i++) {
        if (x_prev > 0.0) {
            /* already boiling: temperature pinned at saturation, quality grows */
            T_target[i] = m->T_sat;
            x_target[i] = x_prev + q_seg / (m_dot * h_fg);
        } else {
            double dT    = q_seg / (m_dot * cp);
            double T_try = T_prev + dT;
            if (T_try <= m->T_sat) {
                T_target[i] = T_try;
                x_target[i] = 0.0;
            } else {
                /* split: sensible heat up to saturation, remainder to vapor */
                double q_sens = m_dot * cp * (m->T_sat - T_prev);
                double q_lat  = q_seg - q_sens;
                if (q_lat < 0.0) q_lat = 0.0;
                T_target[i] = m->T_sat;
                x_target[i] = q_lat / (m_dot * h_fg);
            }
        }
        if (x_target[i] > 1.0) x_target[i] = 1.0;
        T_prev = T_target[i];
        x_prev = x_target[i];
    }

    /* first-order lag toward the target so transients are visible */
    double tau   = m->tau < 1e-3 ? 1e-3 : m->tau;
    double alpha = dt / (tau + dt);
    if (alpha > 1.0) alpha = 1.0;
    for (int i = 0; i < N_SEG; i++) {
        m->T[i] += (T_target[i] - m->T[i]) * alpha;
        m->x[i] += (x_target[i] - m->x[i]) * alpha;
    }

    m->T_out = m->T[N_SEG - 1];
    m->x_out = m->x[N_SEG - 1];

    /* pressure drop: Darcy-Weisbach + simple homogeneous two-phase multiplier */
    double D  = m->D < 1e-4 ? 1e-4 : m->D;
    double A  = M_PI * D * D / 4.0;
    double v  = m_dot / (m->rho * A);
    double mu = 1.0e-3;
    double Re = m->rho * v * D / mu;
    if (Re < 1.0) Re = 1.0;
    double f  = (Re < 2300.0) ? (64.0 / Re) : (0.316 / pow(Re, 0.25));
    double dp_1p = f * (m->L / D) * (m->rho * v * v / 2.0);   /* Pa */
    m->dp_total = dp_1p * (1.0 + 15.0 * m->x_out) / 1000.0;   /* kPa */

    /* absorbed power: sensible + latent */
    m->Q_abs = m_dot * cp * (m->T_out - m->T_in) + m_dot * h_fg * m->x_out;
}

/* Map a value reference to its storage slot. */
static double* slot(PipeModel* m, fmi2ValueReference vr) {
    if (vr >= VR_T_BASE && vr < VR_T_BASE + N_SEG) return &m->T[vr - VR_T_BASE];
    if (vr >= VR_X_BASE && vr < VR_X_BASE + N_SEG) return &m->x[vr - VR_X_BASE];
    switch (vr) {
        case VR_M_DOT:    return &m->m_dot;
        case VR_T_IN:     return &m->T_in;
        case VR_Q_TOTAL:  return &m->Q_total;
        case VR_P_IN:     return &m->p_in;
        case VR_L:        return &m->L;
        case VR_D:        return &m->D;
        case VR_CP:       return &m->cp;
        case VR_H_FG:     return &m->h_fg;
        case VR_T_SAT:    return &m->T_sat;
        case VR_RHO:      return &m->rho;
        case VR_TAU:      return &m->tau;
        case VR_T_OUT:    return &m->T_out;
        case VR_X_OUT:    return &m->x_out;
        case VR_DP_TOTAL: return &m->dp_total;
        case VR_Q_ABS:    return &m->Q_abs;
        default:          return NULL;
    }
}

/* ------------------------------------------------------------- FMI 2.0 API */
FMI2_EXPORT const char* fmi2GetTypesPlatform(void) { return "default"; }
FMI2_EXPORT const char* fmi2GetVersion(void)       { return "2.0"; }

FMI2_EXPORT fmi2Status fmi2SetDebugLogging(fmi2Component c, fmi2Boolean on, size_t n, const fmi2String cat[]) {
    (void)c; (void)on; (void)n; (void)cat; return fmi2OK;
}

FMI2_EXPORT fmi2Component fmi2Instantiate(fmi2String instanceName, fmi2Type fmuType, fmi2String fmuGUID,
                                          fmi2String fmuResourceLocation, const fmi2CallbackFunctions* functions,
                                          fmi2Boolean visible, fmi2Boolean loggingOn) {
    (void)instanceName; (void)fmuType; (void)fmuGUID; (void)fmuResourceLocation;
    (void)functions; (void)visible; (void)loggingOn;
    PipeModel* m = (PipeModel*)calloc(1, sizeof(PipeModel));
    if (m) set_defaults(m);
    return (fmi2Component)m;
}

FMI2_EXPORT void fmi2FreeInstance(fmi2Component c) { if (c) free(c); }

FMI2_EXPORT fmi2Status fmi2SetupExperiment(fmi2Component c, fmi2Boolean tolDefined, fmi2Real tol,
                                           fmi2Real startTime, fmi2Boolean stopDefined, fmi2Real stopTime) {
    (void)tolDefined; (void)tol; (void)stopDefined; (void)stopTime;
    PipeModel* m = (PipeModel*)c;
    if (!m) return fmi2Error;
    m->time = startTime;
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2EnterInitializationMode(fmi2Component c) { (void)c; return fmi2OK; }

FMI2_EXPORT fmi2Status fmi2ExitInitializationMode(fmi2Component c) {
    PipeModel* m = (PipeModel*)c;
    if (!m) return fmi2Error;
    for (int i = 0; i < N_SEG; i++) { m->T[i] = m->T_in; m->x[i] = 0.0; }
    advance(m, 1.0e9);          /* settle to the steady profile at t = 0 */
    m->initialized = 1;
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2Terminate(fmi2Component c) { (void)c; return fmi2OK; }

FMI2_EXPORT fmi2Status fmi2Reset(fmi2Component c) {
    PipeModel* m = (PipeModel*)c;
    if (!m) return fmi2Error;
    set_defaults(m);
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2GetReal(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, fmi2Real value[]) {
    PipeModel* m = (PipeModel*)c;
    if (!m) return fmi2Error;
    for (size_t i = 0; i < nvr; i++) {
        double* p = slot(m, vr[i]);
        if (!p) return fmi2Error;
        value[i] = *p;
    }
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2SetReal(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, const fmi2Real value[]) {
    PipeModel* m = (PipeModel*)c;
    if (!m) return fmi2Error;
    for (size_t i = 0; i < nvr; i++) {
        double* p = slot(m, vr[i]);
        if (!p) return fmi2Error;
        *p = value[i];
    }
    return fmi2OK;
}

/* Integer / Boolean / String: this model is all-Real, but the entry points
   must exist for a spec-conformant FMU. */
FMI2_EXPORT fmi2Status fmi2GetInteger(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2Integer v[]) {
    (void)c; (void)vr; for (size_t i = 0; i < n; i++) v[i] = 0; return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2SetInteger(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2Integer v[]) {
    (void)c; (void)vr; (void)n; (void)v; return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2GetBoolean(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2Boolean v[]) {
    (void)c; (void)vr; for (size_t i = 0; i < n; i++) v[i] = fmi2False; return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2SetBoolean(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2Boolean v[]) {
    (void)c; (void)vr; (void)n; (void)v; return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2GetString(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2String v[]) {
    (void)c; (void)vr; for (size_t i = 0; i < n; i++) v[i] = ""; return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2SetString(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2String v[]) {
    (void)c; (void)vr; (void)n; (void)v; return fmi2OK;
}

/* FMU state: the whole model is a POD struct, so save/restore is a memcpy.
   These entry points are resolved by importers (e.g. FMPy) regardless of the
   capability flags, so they are implemented rather than omitted. */
FMI2_EXPORT fmi2Status fmi2GetFMUstate(fmi2Component c, fmi2FMUstate* s) {
    PipeModel* m = (PipeModel*)c;
    if (!m || !s) return fmi2Error;
    PipeModel* copy = (PipeModel*)(*s ? *s : malloc(sizeof(PipeModel)));
    if (!copy) return fmi2Error;
    memcpy(copy, m, sizeof(PipeModel));
    *s = (fmi2FMUstate)copy;
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2SetFMUstate(fmi2Component c, fmi2FMUstate s) {
    PipeModel* m = (PipeModel*)c;
    if (!m || !s) return fmi2Error;
    memcpy(m, s, sizeof(PipeModel));
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2FreeFMUstate(fmi2Component c, fmi2FMUstate* s) {
    (void)c;
    if (s && *s) { free(*s); *s = NULL; }
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2SerializedFMUstateSize(fmi2Component c, fmi2FMUstate s, size_t* size) {
    (void)c; (void)s;
    if (!size) return fmi2Error;
    *size = sizeof(PipeModel);
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2SerializeFMUstate(fmi2Component c, fmi2FMUstate s, fmi2Byte b[], size_t size) {
    (void)c;
    if (!s || !b || size < sizeof(PipeModel)) return fmi2Error;
    memcpy(b, s, sizeof(PipeModel));
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2DeSerializeFMUstate(fmi2Component c, const fmi2Byte b[], size_t size, fmi2FMUstate* s) {
    (void)c;
    if (!b || !s || size < sizeof(PipeModel)) return fmi2Error;
    PipeModel* copy = (PipeModel*)malloc(sizeof(PipeModel));
    if (!copy) return fmi2Error;
    memcpy(copy, b, sizeof(PipeModel));
    *s = (fmi2FMUstate)copy;
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2GetDirectionalDerivative(fmi2Component c, const fmi2ValueReference unk[], size_t nUnk,
                                                    const fmi2ValueReference known[], size_t nKnown,
                                                    const fmi2Real dvKnown[], fmi2Real dvUnk[]) {
    (void)c; (void)unk; (void)nUnk; (void)known; (void)nKnown; (void)dvKnown; (void)dvUnk;
    return fmi2Error;   /* not provided; declared as such in modelDescription.xml */
}

/* Input/output derivatives: not supported (maxOutputDerivativeOrder = 0), but the
   symbols must exist for importers that resolve the full CS entry-point table. */
FMI2_EXPORT fmi2Status fmi2SetRealInputDerivatives(fmi2Component c, const fmi2ValueReference vr[], size_t n,
                                                   const fmi2Integer order[], const fmi2Real value[]) {
    (void)c; (void)vr; (void)n; (void)order; (void)value;
    return fmi2Error;
}

FMI2_EXPORT fmi2Status fmi2GetRealOutputDerivatives(fmi2Component c, const fmi2ValueReference vr[], size_t n,
                                                    const fmi2Integer order[], fmi2Real value[]) {
    (void)c; (void)vr; (void)order;
    for (size_t i = 0; i < n; i++) value[i] = 0.0;
    return fmi2Error;
}

FMI2_EXPORT fmi2Status fmi2DoStep(fmi2Component c, fmi2Real currentCommunicationPoint,
                                  fmi2Real communicationStepSize, fmi2Boolean noSetPrior) {
    (void)noSetPrior;
    PipeModel* m = (PipeModel*)c;
    if (!m) return fmi2Error;
    if (communicationStepSize < 0.0) return fmi2Error;
    advance(m, communicationStepSize);
    m->time = currentCommunicationPoint + communicationStepSize;
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2CancelStep(fmi2Component c) { (void)c; return fmi2OK; }

FMI2_EXPORT fmi2Status fmi2GetStatus(fmi2Component c, const fmi2StatusKind k, fmi2Status* v) {
    (void)c; (void)k; if (v) *v = fmi2OK; return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2GetRealStatus(fmi2Component c, const fmi2StatusKind k, fmi2Real* v) {
    PipeModel* m = (PipeModel*)c;
    if (v) *v = (k == fmi2LastSuccessfulTime && m) ? m->time : 0.0;
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2GetIntegerStatus(fmi2Component c, const fmi2StatusKind k, fmi2Integer* v) {
    (void)c; (void)k; if (v) *v = 0; return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2GetBooleanStatus(fmi2Component c, const fmi2StatusKind k, fmi2Boolean* v) {
    (void)c; (void)k; if (v) *v = fmi2False; return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2GetStringStatus(fmi2Component c, const fmi2StatusKind k, fmi2String* v) {
    (void)c; (void)k; if (v) *v = ""; return fmi2OK;
}
