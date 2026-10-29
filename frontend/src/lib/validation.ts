import { z } from "zod";

export const PHONE_PATTERN = /^\+?[0-9][0-9\s-]{6,20}$/;

export const leadSchema = z.object({
  name: z
    .string()
    .trim()
    .min(2, "Name must be at least 2 characters.")
    .max(255, "Name is too long."),
  phone: z
    .string()
    .trim()
    .min(1, "Phone number is required.")
    .regex(PHONE_PATTERN, "Enter a valid phone number, optionally with country code."),
  source: z
    .string()
    .trim()
    .min(2, "Source is required so the backend can report origin.")
    .max(64, "Source must be 64 characters or fewer."),
  email: z
    .string()
    .trim()
    .max(255)
    .email("Enter a valid email address.")
    .optional()
    .or(z.literal("")),
  language: z.string().optional(),
  deal_value: z
    .string()
    .trim()
    .max(64)
    .regex(/^[0-9.,]*$/, "Use a numeric deal value.")
    .optional()
    .or(z.literal("")),
});

export type LeadFormValues = z.infer<typeof leadSchema>;

export const consentSchema = z.object({
  status: z.enum(["granted", "revoked", "expired"]),
  channel: z.enum(["voice", "sms", "email", "whatsapp"]),
  source: z.string().trim().max(255).optional(),
  granted_at: z.string().optional(),
  expires_at: z.string().optional(),
  note: z.string().trim().max(2000).optional(),
});

export type ConsentFormValues = z.infer<typeof consentSchema>;

export const followupCompletionSchema = z.object({
  outcome: z.string().trim().min(2, "Describe the outcome.").max(2000),
});

export type FollowupCompletionValues = z.infer<typeof followupCompletionSchema>;
