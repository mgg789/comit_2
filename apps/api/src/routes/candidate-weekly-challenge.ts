/** Authenticated weekly practice; clients submit answers, never score or reward timestamps. */
import type { FastifyInstance } from "fastify";
import type { WeeklyChallengeAnswer } from "@comit/shared";
import type { CandidateProfileOptions } from "./candidate-profile.js";
import { originAllowed, resolveSession } from "../auth/session.js";
import { ProfileError } from "../profiles/store.js";
const question = { type:"object",additionalProperties:false,required:["id","prompt","choices"],properties:{id:{type:"string"},prompt:{type:"string"},choices:{type:"array",items:{type:"string"}}} } as const;
const view = { type:"object",additionalProperties:false,required:["week","expires_at","status","questions","correct","passed","completed_at"],properties:{week:{type:"string"},expires_at:{type:"string"},status:{type:"string",enum:["available","started","completed"]},questions:{type:"array",items:question},correct:{type:["integer","null"]},passed:{type:["boolean","null"]},completed_at:{type:["string","null"]}} } as const;
/** Register read, start and once-per-week submission under the candidate session. */
export async function candidateWeeklyChallengeRoutes(app: FastifyInstance, options: CandidateProfileOptions): Promise<void> {
 for (const operation of ["read","start","submit"] as const) app.route<{Body:WeeklyChallengeAnswer}>({
  method:operation==="read"?"GET":"POST",url:`/candidate/weekly-challenge${operation==="read"?"":`/${operation}`}`,
  schema:{tags:["candidate-weekly-challenge"],security:[{candidateSession:[]},{bearerAuth:[]}],response:{200:view},
   ...(operation==="submit"?{body:{type:"object",additionalProperties:false,required:["week","answers"],properties:{week:{type:"string",pattern:"^\\d{4}-\\d{2}-\\d{2}$"},answers:{type:"object",minProperties:3,maxProperties:3,additionalProperties:{type:"string",maxLength:80}}}}}:operation==="start"?{body:{type:"object",additionalProperties:false,properties:{}}}:{})},
  handler:async(request,reply)=>{
   reply.header("cache-control","no-store");
   try {
    const identity=await resolveSession(request,"candidate",options.config,options.store);
    if(!identity) return reply.code(401).send({detail:"unauthenticated"});
    if(operation!=="read"&&!originAllowed(request,options.config,identity.transport==="bearer"))return reply.code(403).send({detail:"origin_forbidden"});
    return await options.profiles.weeklyChallenge(identity.accountId,operation,request.body);
   } catch(error) { if(error instanceof ProfileError)return reply.code(error.status).send({detail:error.code}); return reply.code(503).send({detail:"weekly_task_unavailable"}); }
  }
 });
}
