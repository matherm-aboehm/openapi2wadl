#!/usr/bin/env python3
# openapi2wadl.py
# Converts Swagger 2.0 or OpenAPI 3.0 into WADL + XSD

# ####################################################################################################
# Referenze
# ####################################################################################################

import os
import re
import sys
import json
import enum
import argparse
import xml.etree.ElementTree as ET
from xml.dom import minidom

# ####################################################################################################
# Definizione costanti e namespace
# ####################################################################################################

OSB_PATH = ""
NULL_MODE = "nillable"
ARRAY_MODE = "inline"
ROOT_MODE = "method"

SERVICE_NAME = "MyServiceName"
SERVICE_VERSION = "1.0"

SOA_PREFIX = "soa"
SOA_NAMESPACE = "http://www.oracle.com/soa/rest"

XSD_PREFIX = "xsd"
XSD_NAMESPACE = "http://www.w3.org/2001/XMLSchema"

WADL_PREFIX = ""
WADL_NAMESPACE = "http://wadl.dev.java.net/2009/02"

WSDL_PREFIX = "wsdl"
WSDL_NAMESPACE = "http://schemas.xmlsoap.org/wsdl/"

SOAP_PREFIX = "soap"
SOAP_NAMESPACE = "http://schemas.xmlsoap.org/wsdl/soap/"

TARGET_PREFIX = "tns"
TARGET_NAMESPACE = "http://example.com/schema"

ET.register_namespace(SOA_PREFIX, SOA_NAMESPACE)
ET.register_namespace(XSD_PREFIX, XSD_NAMESPACE)
ET.register_namespace(WADL_PREFIX, WADL_NAMESPACE)
ET.register_namespace(WSDL_PREFIX, WSDL_NAMESPACE)
ET.register_namespace(SOAP_PREFIX, SOAP_NAMESPACE)
ET.register_namespace(TARGET_PREFIX, TARGET_NAMESPACE)

# ####################################################################################################
# Migliorare leggibilità xml
# ####################################################################################################
def prettify_xml(elem):

    rough_string = ET.tostring(elem, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    pretty = reparsed.toprettyxml(indent="   ")

    # Correggi solo gli attributi name con spazi interni
    fixed = re.sub(r'name="([A-Za-z0-9_]+)(\s+)"', r'name="\1"\2', pretty)

    # Sposta in fondo minOccurs, maxOccurs, nillable sugli "element"
    fixed = re.sub(r'<(.+?)(?=\snillable)(\snillable="[^"]*")([^\/|>]*)(\/)?>', r'<\1\3\2\4>', fixed)
    fixed = re.sub(r'<(.+?)(?=\sminOccurs)(\sminOccurs="[^"]*")([^\/|>]*)(\/)?>', r'<\1\3\2\4>', fixed)
    fixed = re.sub(r'<(.+?)(?=\smaxOccurs)(\smaxOccurs="[^"]*")([^\/|>]*)(\/)?>', r'<\1\3\2\4>', fixed)

    # compatta operations wsdl
    fixed = re.sub(r'<wsdl:input>[^<>]*<soap:body use="literal"/>[^<>]*</wsdl:input>', r'<wsdl:input><soap:body use="literal"/></wsdl:input>',fixed,flags=re.DOTALL)
    fixed = re.sub(r'<wsdl:output>[^<>]*<soap:body use="literal"/>[^<>]*</wsdl:output>', r'<wsdl:output><soap:body use="literal"/></wsdl:output>',fixed,flags=re.DOTALL)
    
    # Sposta in fondo id su "method"
    fixed = re.sub(r'<(method)(\sid="[^"]*")([^\/|>]*)(\/)?>', r'<\1\3\2\4>', fixed)

    # Rimuove eventuali spazi finali dell'elemento
    fixed = re.sub(r'"\s*>', r'">', fixed)

    return fixed

# ####################################################################################################
# Rileva la versione della specifica
# ####################################################################################################
def detect_version(spec):

    if "swagger" in spec and spec["swagger"] == "2.0":
        return "swagger2"
    elif "openapi" in spec and spec["openapi"].startswith("3."):
        return "openapi3"
    else:
        # genera eccezione    
        print("Unsupported OpenAPI/Swagger version")
        sys.exit()

# ####################################################################################################
# Estrare la definizione dei parametri riusabili
# ####################################################################################################
def extract_root_responses(spec, version):

    if version == "swagger2":
        return spec.get("responses", {})
    else:
        return spec.get("components", {}).get("responses", {})
        
# ####################################################################################################
# Estrare la definizione dei parametri riusabili
# ####################################################################################################
def extract_root_parameters(spec, version):

    if version == "swagger2":
        return spec.get("parameters", {})
    else:
        return spec.get("components", {}).get("parameters", {})
        
# ####################################################################################################
# Estrare la definizione dei schema riusabili
# ####################################################################################################
def extract_root_schemas(spec, version):

    if version == "swagger2":
        return spec.get("definitions", {})
    else:
        return spec.get("components", {}).get("schemas", {})
        
# ####################################################################################################
# Risolve i $ref dei parametri
# ####################################################################################################       
def resolve_ref_responses(response, root_responses):

    # se è un $ref esegue
    if "$ref" in response:    
        type_name = response.get("$ref").split("/")[-1]
        response = root_responses.get(type_name, {}).copy()
        
    # restisuisce risultato
    return response
    
# ####################################################################################################
# Risolve i $ref dei parametri
# ####################################################################################################       
def resolve_ref_parameters(parameter, root_parameters):

    # se è un $ref esegue
    if "$ref" in parameter:    
        type_name = parameter.get("$ref").split("/")[-1]
        parameter = root_parameters.get(type_name, {}).copy()
        
    # restisuisce risultato
    return parameter
      
# ####################################################################################################
# Risolve ricorsivamente gli $ref, evitando loop infiniti e unendo le proprietà
# ####################################################################################################       
def resolve_ref(schema, root_schemas, seen=None):

    # se è un $ref esegue
    if "$ref" in schema:
    
        # determina il tipo referenziato
        type_name = schema.get("$ref").split("/")[-1]
        
        # se il controllo dei loop non è inizializzato provvede, altrimenti se il tipo è già stato visitato esce per evitare i loop
        if seen is None:
            seen = set()           
        elif type_name in seen:
            return {}  
            
        # aggiunge il tipo al controllo dei loop
        seen.add(type_name)

        # determina ricorsivamente il primo schema della catena nidificata di $ref
        resolved = root_schemas.get(type_name, {}).copy()        
        nested = resolve_ref(resolved, root_schemas, seen)
        
        # restituisce 
        return {**nested, **schema}  # priorità a schema locale

    # restisuisce immodificato lo schema in ingresso
    return schema

# ####################################################################################################
# Recursively resolves allOf, avoiding infinite loops and merging properties.
# This is required as a workaround for the missing intersection concept in XSD.
# ####################################################################################################       
def resolve_allOf(schema, root_schemas, seen=None):
    
    if "allOf" in schema:
        
        schema = schema.copy()
        all_schemas = schema.pop("allOf")
        
        for sub_schema in all_schemas:
            
            # resolves all $refs in sub schema
            sub_schema = resolve_ref(sub_schema, root_schemas).copy()

            # remove the $ref item from sub schema, because the parent should not know of that
            sub_schema.pop("$ref", None)
            
            # recurse into resolving allOf of sub schema
            sub_schema = resolve_allOf(sub_schema, root_schemas)
            
            # TODO: check for unmergable properties like "type" before doing the merge
            schema = {**sub_schema, **schema}
            
    return schema

# ####################################################################################################
# Acquisce le restrizioni dalle proprietà dal swagger/openapi
# ####################################################################################################
def get_restrictions(schema):

    type_name = schema.get("type")
    
    if (type_name in ["number","integer"]):
        return {
            k: schema[k]
            for k in ["minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"] if k in schema
        }
    
    elif (type_name=="string"):
        return {
            k: schema[k]
            for k in ["minLength", "maxLength", "pattern", "enum"] if k in schema
        }    
        
    else:
        return {}        
    
# ####################################################################################################
# Esegue mapping delle restrizioni da swagger/openapi a XSD
# ####################################################################################################
def map_restrictions(element, schema):

    if "pattern" in schema:
        ET.SubElement(element, f"{{{XSD_NAMESPACE}}}pattern", value=schema["pattern"])
        
    if "minLength" in schema:
        ET.SubElement(element, f"{{{XSD_NAMESPACE}}}minLength", value=str(schema["minLength"]))
        
    if "maxLength" in schema:
        ET.SubElement(element, f"{{{XSD_NAMESPACE}}}maxLength", value=str(schema["maxLength"]))
        
    if "minimum" in schema:
        if schema.get("exclusiveMinimum",False):
            ET.SubElement(element, f"{{{XSD_NAMESPACE}}}minExclusive", value=str(schema["minimum"]))
        else:
            ET.SubElement(element, f"{{{XSD_NAMESPACE}}}minInclusive", value=str(schema["minimum"]))

    if "maximum" in schema:
        if schema.get("exclusiveMaximum",False):
            ET.SubElement(element, f"{{{XSD_NAMESPACE}}}maxExclusive", value=str(schema["maximum"]))
        else:
            ET.SubElement(element, f"{{{XSD_NAMESPACE}}}maxInclusive", value=str(schema["maximum"]))
            
    if "enum" in schema:
        for value in schema.get("enum"):
           ET.SubElement(element, f"{{{XSD_NAMESPACE}}}enumeration", value=str(value if value!=None else ""))            

# ####################################################################################################
# Gestisce mapping della nullability dei tipi atomici
# ####################################################################################################
def map_nullability(schema, type_prefix, type_name, nullability_registry, restriction_registry):
   
    if (not schema.get("nullable",False)) or (NULL_MODE=="nillable"):
        return f"{type_prefix}:{type_name}"
    else:        
        nillable_type = f"{type_name}Nillable"
    
        if not nillable_type in nullability_registry:
           simple_type = ET.Element(f"{{{XSD_NAMESPACE}}}simpleType", name=nillable_type)
           union = ET.SubElement(simple_type, f"{{{XSD_NAMESPACE}}}union", memberTypes=f"{type_prefix}:{type_name} {TARGET_PREFIX}:emptyString")
           nullability_registry[nillable_type] = simple_type

        schema.pop("nullable")
        
        return f"{TARGET_PREFIX}:{nillable_type}"

# ####################################################################################################
# Esegue mapping dei tipi atomici swagger/openapi a XSD
# ####################################################################################################
def map_type_atomic(schema):
        
    # acquisisce gli attributi del tipo
    type_prefix = XSD_PREFIX
    type_name = schema.get("type","")
    type_format = schema.get("format","")
        
    # gestisce tipi boolean
    if type_name == "boolean":
        return f"{type_prefix}:{type_name}"
                                    
    # gestisce tipi byte
    if type_name == "string" and type_format == "byte":
        return type_prefix+":base64Binary"

    # gestisce fomati data stringa
    if type_name == "string" and type_format in ["date", "date-time"]:
        return type_prefix+":"+("dateTime" if type_format == "date-time" else "date")

    # gestisce tipi stringa
    if type_name == "string":
        return f"{type_prefix}:{type_name}"

    # gestisce tipi numerici
    if (((type_name == "number") and (type_format in ["","float","double"])) or
        ((type_name == "integer") and (type_format in ["","int32","int64"]))):  
    
        # corregge tipo in base all'eventuale specificatore di formato
        if (type_name=="number"):
            type_name = "decimal" if type_format == "" else type_format
        else:        
            type_name = "integer" if type_format == "" else "int" if type_format == "int32" else "long"  
        
        return f"{type_prefix}:{type_name}"
            
    # genera eccezione    
    print("Unsupported type: ",schema)
    sys.exit()

# ####################################################################################################
# Esegue mapping dei tipi swagger/openapi a XSD (crea tipi riusabili in presenza di retrizioni)
# ####################################################################################################
def map_type(schema, nullability_registry, restriction_registry):
        
    # acquisisce gli attributi del tipo
    type_prefix = XSD_PREFIX
    type_name = schema.get("type","")
    type_format = schema.get("format","")
    type_nullable = schema.get("nullable",False)
    type_restrictions = get_restrictions(schema)
        
    # gestisce tipi boolean
    if type_name == "boolean":
        return map_nullability(schema,type_prefix,type_name,nullability_registry,restriction_registry)
                                    
    # gestisce tipi byte
    if type_name == "string" and type_format == "byte":
        return map_nullability(schema,type_prefix,"base64Binary",nullability_registry,restriction_registry)

    # gestisce fomati data stringa
    if type_name == "string" and type_format in ["date", "date-time"]:
        return map_nullability(schema,type_prefix,"dateTime" if type_format == "date-time" else "date",nullability_registry,restriction_registry)

    # gestisce tipi stringa
    if type_name == "string":
    
        # acquisisce eventuali restrizioni sui limiti
        min_len = type_restrictions.get("minLength", 0)
        max_len = type_restrictions.get("maxLength", "")

        # costruisce nome tipo riusabile
        pre_part = "emptyString" if max_len==0 else "openString" if not isinstance(max_len, int) else "string"       
        min_part = f"{min_len}" if isinstance(min_len, int) and min_len>1 else ""
        max_part = f"{max_len}" if isinstance(max_len, int) and max_len>0 else ""
        sep_part = "to" if min_part!="" and max_part!="" else ""  
        end_part = "Nillable" if min_len==0 else ""        
        type_name = pre_part+min_part+sep_part+max_part+end_part
                        
        # se non è già definito predispone simple type XML del tipo riusabile
        if not type_name in restriction_registry:
            simple_type = ET.Element(f"{{{XSD_NAMESPACE}}}simpleType", name=type_name)
            restriction = ET.SubElement(simple_type, f"{{{XSD_NAMESPACE}}}restriction", base=f"{XSD_PREFIX}:string")
            map_restrictions(restriction, dict(filter(lambda item: item[0] in {"minLength","maxLength"}, type_restrictions.items())))        
            restriction_registry[type_name] = simple_type        

        # rimuove dallo schema le restrizioni mappate sul tipo riusabile (che non è necessario rigestire nel rendering dell'elemento)
        schema.pop("minLength",None)
        schema.pop("maxLength",None)

        return f"{TARGET_PREFIX}:{type_name}"

    # gestisce tipi numerici
    if (((type_name == "number") and (type_format in ["","float","double"])) or
        ((type_name == "integer") and (type_format in ["","int32","int64"]))):  
    
        # corregge tipo in base all'eventuale specificatore di formato
        if (type_name=="number"):
            type_name = "decimal" if type_format == "" else type_format
        else:        
            type_name = "integer" if type_format == "" else "int" if type_format == "int32" else "long"  
        
        # acquisisce eventuali restrizioni sui limiti
        min_val = type_restrictions.get("minimum","")
        max_val = type_restrictions.get("maximum","")
        min_excl = type_restrictions.get("exclusiveMinimum",False)
        max_excl = type_restrictions.get("exclusiveMaximum",False)
                   
        # se ci sono restrizioni ridondanti sui limiti superiori le rimuove per semplificare la eventuale definizione dei tipi riusabili
        if (((type_name=="int") and 
             (((max_val == 2147483648) and (max_excl==True)) or
              ((max_val == 2147483647) and (max_excl==False)))) or
            ((type_name=="long") and 
             (((max_val == 9223372036854775808) and (max_excl==True)) or
              ((max_val == 9223372036854775807) and (max_excl==False))))):
             
            max_val = ""
            type_restrictions.pop("maximum",None)
            type_restrictions.pop("exclusiveMaximum",None)
                                          
        # se ci sono restrizioni sui limiti introduce tipo riusabile
        if (min_val!="") or (max_val!=""):

            # imposta il prefix dei tipi riusabili e salva il nome del tipo atomico
            type_prefix = TARGET_PREFIX            
            atomic_name = type_name
        
            # costruisce nome tipo riusabile
            min_part = ("Gt"+("" if min_excl else "e")+f"{min_val}") if isinstance(min_val, int) else ""
            max_part = ("Lt"+("" if max_excl else "e")+f"{max_val}") if isinstance(max_val, int) else ""          
            end_part = min_part+max_part          
            
            # ridenomina alcuni tipi
            if end_part == "Gt0":
                type_name = "positive"+type_name.capitalize()
            elif end_part == "Gte0":
                type_name = "nonNegative"+type_name.capitalize()
            elif end_part == "Lt0":
                type_name = "negative"+type_name.capitalize()
            elif end_part == "Lte0":
                type_name = "nonPositive"+type_name.capitalize()
            else:
                type_name = type_name+end_part
                
            # se non è già definito predispone simple type XML del tipo riusabile
            if not type_name in restriction_registry:            
                simple_type = ET.Element(f"{{{XSD_NAMESPACE}}}simpleType", name=type_name)
                restriction = ET.SubElement(simple_type, f"{{{XSD_NAMESPACE}}}restriction", base=f"{XSD_PREFIX}:{atomic_name}")
                map_restrictions(restriction, dict(filter(lambda item: item[0] in {"minimum","maximum","exclusiveMinimum","exclusiveMaximum"}, type_restrictions.items())))            
                restriction_registry[type_name] = simple_type
                       
            # rimuove dallo schema le restrizioni mappate sul tipo riusabile (che non è necessario rigestire nel rendering dell'elemento)
            schema.pop("minimum",None)
            schema.pop("maximum",None)
            schema.pop("exclusiveMinimum",None)
            schema.pop("exclusiveMaximum",None)

        return map_nullability(schema,type_prefix,type_name,nullability_registry,restriction_registry)
            
    # genera eccezione    
    print("Unsupported type: ",schema)
    sys.exit()
    
# ####################################################################################################
# Genera Element/SimpleType
# ####################################################################################################    
def generate_xsd_simple_type(level, parent_element, root_name, schema, nullability_registry, restriction_registry):

    # se si tratta di un ref lo gestisce ad hoc
    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        parent_element.set('type',f"{TARGET_PREFIX}:{ref_name}")
        return

    # se necessario aggiunge attributo di nullability
    if schema.get("nullable", False) and NULL_MODE!="union":
       parent_element.set('nillable',"true")    
            
    # determina il tipo xsd più appropriato 
    mapped_type = map_type(schema,nullability_registry,restriction_registry)

    # riacquisisce parametri tipo 
    type_nullable = schema.get("nullable", False) and NULL_MODE!="nillable"    
    type_restrictions = get_restrictions(schema)
    simple_type = None
    
    # crea il nodo xml appropriato al tipo dell'elemento
    if not type_restrictions:
    
        if not type_nullable:
            if root_name=="":
                parent_element.set('type',mapped_type)
            else:
                simple_type = ET.SubElement(parent_element, f"{{{XSD_NAMESPACE}}}simpleType")
                ET.SubElement(simple_type, f"{{{XSD_NAMESPACE}}}restriction", base=mapped_type)
        else:
            simple_type = ET.SubElement(parent_element, f"{{{XSD_NAMESPACE}}}simpleType")
            union = ET.SubElement(simple_type, f"{{{XSD_NAMESPACE}}}union", memberTypes=f"{mapped_type} {TARGET_PREFIX}:emptyString")    
    
    elif not type_nullable:
        simple_type = ET.SubElement(parent_element, f"{{{XSD_NAMESPACE}}}simpleType")
        restriction = ET.SubElement(simple_type, f"{{{XSD_NAMESPACE}}}restriction", base=mapped_type)
        map_restrictions(restriction, type_restrictions)
    else:
        simple_type = ET.SubElement(parent_element, f"{{{XSD_NAMESPACE}}}simpleType")
        union = ET.SubElement(simple_type, f"{{{XSD_NAMESPACE}}}union", memberTypes=f"{TARGET_PREFIX}:emptyString")            
        inline = ET.SubElement(union, f"{{{XSD_NAMESPACE}}}simpleType")
        restriction = ET.SubElement(inline, f"{{{XSD_NAMESPACE}}}restriction", base=mapped_type)
        map_restrictions(restriction, type_restrictions)

    # se è un nodo radice aggiunge l'attributo del nome
    if (root_name!="" and simple_type is not None):
        simple_type.set("name",root_name)


# ####################################################################################################
# Genera ComplexType
# ####################################################################################################
def generate_xsd_type(level, parent_element, root_name, def_body, root_schemas, nullability_registry, restriction_registry):

    # verifica se si tratta di un $ref
    def_ref = def_body.get("$ref","");                    

    # risolve eventuali $ref sullo schema body
    def_body = resolve_ref(def_body, root_schemas)

    # handle the allOf by merging all properties together as a workaround
    def_body = resolve_allOf(def_body, root_schemas)

    #  determina il tipo dello schema
    def_type = def_body.get("type", "object");    
    
    # se si tratta di un array esegue
    if def_ref=="" and def_type == "array":
                
        # acquisisce eventuali limiti dell'array
        min_len = def_body.get("minItems","0")
        max_len = def_body.get("maxItems","unbounded")
        array_type = None

        # crea nodi per array
        if ARRAY_MODE=="inline":
            array_element = parent_element
            array_element.set("minOccurs",f"{min_len}")
            array_element.set("maxOccurs",f"{max_len}")
        else:
            array_type = ET.SubElement(parent_element,f"{{{XSD_NAMESPACE}}}complexType")             
            array_sequence = ET.SubElement(array_type, f"{{{XSD_NAMESPACE}}}sequence")
            array_element = ET.SubElement(array_sequence, f"{{{XSD_NAMESPACE}}}element", attrib={
                "name": "item", "minOccurs": f"{min_len}", "maxOccurs": f"{max_len}"
            })
        
        # se è un nodo radice aggiunge l'attributo del nome
        if (root_name!="" and array_type is not None):
           array_type.set("name",root_name)
        
        # genera definizione del tipo in modo ricorsivo
        generate_xsd_type(level+1,array_element,"",def_body.get("items", {}),root_schemas,nullability_registry,restriction_registry)               
    
    # se si tratta di un object esegue
    elif def_ref=="" and def_type == "object":
                         
        # determina attributi accessori dell'object    
        def_required = def_body.get("required", [])
        def_properties = def_body.get("properties", {})

        # determina padding per i type degli elementi
        name_padding = max([len(p) for p, a in def_properties.items() if a.get("type") != "array" or ARRAY_MODE=="inline"] or [0])
           
        # crea nodi per complex type
        complex_type = ET.SubElement(parent_element,f"{{{XSD_NAMESPACE}}}complexType")             
        sequence = ET.SubElement(complex_type, f"{{{XSD_NAMESPACE}}}sequence")

        # se è un nodo radice aggiunge l'attributo del nome
        if (root_name!=""):
           complex_type.set("name",root_name)

        # esegue un ciclo su tutte le proprietà del complex type
        for prop_name, prop_attrs in def_properties.items():
                        
            # crea attributi per nodo 
            element_attrib = {"name": prop_name}
            
            # verifica e gestisce se l'elemento non è obbligatorio
            if prop_name not in def_required:
                element_attrib["minOccurs"] = "0"

                if NULL_MODE!="union":
                    element_attrib["nillable"] = "true"

            # acquisisce attributi proprietà
            prop_ref = prop_attrs.get("$ref",""); 
            prop_type = prop_attrs.get("type"); 
                
            # se non si tratta $ref ed è un tipo array o object esegue, altrimenti procede
            if prop_ref=="" and prop_type in ["array","object"]:
            
                # se necessario corregge nome elemento per introdurre padding
                if prop_type=="array" and ARRAY_MODE=="inline":
                    element_attrib["name"] = element_attrib["name"] + (" " * (name_padding-len(prop_name)))
                                                    
                # crea nodo per elemento di tipo array
                complex_element = ET.SubElement(sequence,f"{{{XSD_NAMESPACE}}}element", attrib=element_attrib)
                
                # genera definizione del tipo
                generate_xsd_type(level+1,complex_element,"",prop_attrs,root_schemas,nullability_registry,restriction_registry)
                
            else:
            
                # corregge nome elemento per introdurre padding
                element_attrib["name"] = element_attrib["name"] + (" " * (name_padding-len(prop_name)))
                    
                # crea nodo per elemento semplice
                simple_element = ET.SubElement(sequence,f"{{{XSD_NAMESPACE}}}element", attrib=element_attrib)
                
                # genera definizione del tipo
                generate_xsd_simple_type(level,simple_element,"",prop_attrs,nullability_registry,restriction_registry)                                

    else:

        # genera definizione del tipo
        generate_xsd_simple_type(level,parent_element,root_name,def_body,nullability_registry,restriction_registry)  

# ####################################################################################################
# Genera il file XSD
# ####################################################################################################
def generate_xsd(root_schemas,element_registry,nullability_registry,restriction_registry):

    # funzione di supporto per ordinamento dei simple type per le restriction
    def sorting_criteria(restriction_element):
    
        min_len = None
        max_len = None
        type_order = None
        
        for child in restriction_element.iter():
            if child.tag.endswith("restriction"):
                type_order = child.attrib.get("base")
            if child.tag.endswith("minLength"):
                min_len = int(child.attrib.get("value", "0"))
            if child.tag.endswith("maxLength"):
                max_len = int(child.attrib.get("value", str(float('inf'))))
                
        return (type_order, max_len or float('inf'), min_len or 0)

    # prepara variabili di lavoro
    complex_types = ET.Element("root")
    element_declarations = []

    # genera nodo radice dell'XSD
    schema = ET.Element(f"{{{XSD_NAMESPACE}}}schema", attrib={
        "targetNamespace": TARGET_NAMESPACE,
        "elementFormDefault": "unqualified",
        f"xmlns:{TARGET_PREFIX}": TARGET_NAMESPACE
    })

    # ================================================================================================
    # Prepara Complex Types
    # ================================================================================================

    # Esegue un ciclo su tutti i tipi definiti al primo livello del contract
    for idx, (def_name, def_body) in enumerate(root_schemas.items()):

        # se necessario crea separatore tra i complex type
        if idx > 0:
            complex_types.append(ET.Comment(" ~~~~~~~~ "))

        # genera il prossimo complex type
        generate_xsd_type(0,complex_types, def_name, def_body, root_schemas, nullability_registry, restriction_registry)
                            
    # ================================================================================================
    # Genera Special Types
    # ================================================================================================
    schema.append(ET.Comment("#" * 100))
    schema.append(ET.Comment(" SimpleTypes for nullability of atomic types"))
    schema.append(ET.Comment("#" * 100))

    empty_string = ET.Element(f"{{{XSD_NAMESPACE}}}simpleType", name="emptyString")
    restriction = ET.SubElement(empty_string, f"{{{XSD_NAMESPACE}}}restriction", base=f"{XSD_PREFIX}:string")
    ET.SubElement(restriction, f"{{{XSD_NAMESPACE}}}length", value="0")
    schema.append(empty_string)
    
    for element in nullability_registry.values():    
        schema.append(ET.Comment(" ~~~~~~~~ "))
        schema.append(element)

    # ================================================================================================
    # Genera Reusable Types
    # ================================================================================================
    schema.append(ET.Comment("#" * 100))
    schema.append(ET.Comment(" SimpleTypes for reusable restrictions"))
    schema.append(ET.Comment("#" * 100))

    sorted_simpletypes = sorted(
        restriction_registry.items(),
        key=lambda item: (
            sorting_criteria(item[1])[0], 
            sorting_criteria(item[1])[1],
            sorting_criteria(item[1])[2]
            #item[0].lower()[0]
        )
    )
    
    for idx, (type_name, restriction) in enumerate(sorted_simpletypes):
    
        if idx > 0:
            schema.append(ET.Comment(" ~~~~~~~~ "))
            
        schema.append(restriction)
        
    # ================================================================================================
    # Genera Complex Types
    # ================================================================================================
    schema.append(ET.Comment("#" * 100))
    schema.append(ET.Comment(" ComplexTypes for schema definitions"))
    schema.append(ET.Comment("#" * 100))

    for child in complex_types:
       schema.append(child);

    # ================================================================================================
    # Genera Element di interfaccia
    # ================================================================================================
    schema.append(ET.Comment("#" * 100))
    schema.append(ET.Comment(" Elements for interface definitions "))
    schema.append(ET.Comment("#" * 100))

    for idx, (element_name, element_node) in enumerate(element_registry.items()):
    
        # se necessario crea separatore tra gli element
        if idx > 0 and element_name.endswith("Request"):
            schema.append(ET.Comment(" ~~~~~~~~ "))
    
        schema.append(element_node)

    # ================================================================================================

    return schema

# ####################################################################################################
# Genera lo operationId
# ####################################################################################################
def derive_operation_id(path, method_name):

    # Split del path, ignora parti vuote
    parts = [p for p in path.strip('/').split('/') if p]
    
    # Trova l'ultima parte fissa (non {param})
    fixed_parts = [p for p in parts if not (p.startswith('{') and p.endswith('}'))]
    if not fixed_parts:
        return method_name 
    
    return fixed_parts[-1]

# ####################################################################################################
# Genera il file WADL
# ####################################################################################################
def generate_wadl(spec,version,root_responses,root_parameters,root_schemas,xsd_filename,element_registry,nullability_registry,restriction_registry):
    
    application = ET.Element(f"{{{WADL_NAMESPACE}}}application", attrib={
        f"xmlns:{XSD_PREFIX}": XSD_NAMESPACE,
        f"xmlns:{TARGET_PREFIX}": TARGET_NAMESPACE
    })
    
    # ================================================================================================
    # Genera Grammars
    # ================================================================================================
    application.append(ET.Comment("#" * 100))
    application.append(ET.Comment(" Grammars "))
    application.append(ET.Comment("#" * 100))    
    gram = ET.SubElement(application,f"{{{WADL_NAMESPACE}}}grammars")
    ET.SubElement(gram,f"{{{WADL_NAMESPACE}}}include", href=os.path.basename(xsd_filename))

    # ================================================================================================
    # Genera Resources
    # ================================================================================================
    application.append(ET.Comment("#" * 100))
    application.append(ET.Comment(" Resources "))
    application.append(ET.Comment("#" * 100))
    resources = ET.SubElement(application,f"{{{WADL_NAMESPACE}}}resources", base=spec.get("servers", [{}])[0].get("url", "/") if version == "openapi3" else "")

    # ================================================================================================
    # Genera Resource
    # ================================================================================================

    for idx, (path, methods) in enumerate(spec.get("paths", {}).items()):
        
        if idx > 0:
           resources.append(ET.Comment(" ~~~~~~~~ "))
                
        resource = ET.SubElement(resources,f"{{{WADL_NAMESPACE}}}resource", path=path)

        # ------------------------------------------------------------------------------------------------
        # Genera Method
        # ------------------------------------------------------------------------------------------------
        
        for method_name, method_def in methods.items():
        
            # Acquisisce proprietà del metodo
            produces = method_def.get("produces", []) 
            responses = method_def.get("responses", {})
            parameters = method_def.get("parameters", [])
            operationId = method_def.get("operationId", "").strip()

            # Se manca operationId lo ricava dal path estraendone l'ultimo token ignorando eventuali parametri
            if not operationId:
                operationId = derive_operation_id(path,method_name)

            # Genera elemento XML del metodo
            method = ET.SubElement(resource, f"{{{WADL_NAMESPACE}}}method", name=method_name.upper(), id=operationId, attrib={f"{{{SOA_NAMESPACE}}}wsdlOperation":operationId})
                                                                                    
            # ------------------------------------------------------------------------------------------------
            # Genera Request
            # ------------------------------------------------------------------------------------------------ 

            # Prepara nomi per gli element di interfaccia
            request_name = operationId+"Request"

            # Genera elemento WADL della request del metodo
            request_elem = ET.SubElement(method,f"{{{WADL_NAMESPACE}}}request")

            # Prepara elemento XSD di request dell'operation
            request_node = ET.Element(f"{{{XSD_NAMESPACE}}}element", name=request_name)
            element_registry[request_name] = request_node            
            sequence = None

            # Gestione dei parameters diversi da body
            for param in parameters:

                # Se si tratta di un parametro $ref lo risolve
                param = resolve_ref_parameters(param,root_parameters)
            
                # Acquisisce attributi parametro
                param_name = param.get("name")
                param_style = param.get("in", "query")
                param_required = param.get("required", False)
                
                # Se necessario mappa style del parametro
                if param_style == "path":
                    param_style = "template"
                elif not param_style in ["query", "header", "matrix"]:
                    continue
                
                # Acquisisce lo schema del parametro
                if not "schema" in param:
                   schema = param
                else:
                   schema = param.get("schema")
                   
                # Se necessario mappa il tipo del parametro
                param_type = map_type_atomic(schema)
                
                # Aggiunge parametro all'elemento WADL                
                ET.SubElement(request_elem,f"{{{WADL_NAMESPACE}}}param", name=param_name, style=param_style, type=param_type, required=str(param_required).lower(),attrib={
                    f"{SOA_PREFIX}:expression": "$msg.parameters/"+param_name
                })     
                
                # Aggiunge parametro all'elemento XSD
                if not sequence:
                    complex_type = ET.SubElement(request_node,f"{{{XSD_NAMESPACE}}}complexType")
                    sequence = ET.SubElement(complex_type,f"{{{XSD_NAMESPACE}}}sequence")
                
                param_elem = ET.SubElement(sequence,f"{{{XSD_NAMESPACE}}}element", name=param_name, type=param_type) 
                
                if not param_required:
                   param_elem.set("minOccurs","0");
                    
            # Gestione dei representation di request body (swagger2)
            if version == "swagger2":
                        
                consumes = method_def.get("consumes", []) 

                for param in parameters:
                    if param.get("in")=="body":
                
                        schema_ref = param.get("schema", {}).get("$ref")
                    
                        # Se non è uno schema $ref non lo gestisce e aggiunge solo elemento WADL, altrimenti procede
                        if not schema_ref:
                            for media_type in consumes: 
                                ET.SubElement(request_elem,f"{{{WADL_NAMESPACE}}}representation", mediaType=media_type)  
                        else:
                            type_name = schema_ref.split("/")[-1]      
                            
                            for media_type in consumes:          

                                # Aggiunge body all'elemento WADL                                            
                                ET.SubElement(request_elem,f"{{{WADL_NAMESPACE}}}representation", mediaType=media_type, element=f"{TARGET_PREFIX}:{request_name if ROOT_MODE!="type" else type_name}")
                                
                                # Aggiunge body all'elemento XSD
                                if not sequence:
                                    request_node.set("type",f"{TARGET_PREFIX}:{type_name}")
                                    if ROOT_MODE=="type": request_node.set("name", type_name)
                                else:
                                   ET.SubElement(sequence,f"{{{XSD_NAMESPACE}}}element", name=request_name if ROOT_MODE!="type" else type_name, type=f"{TARGET_PREFIX}:{type_name}")                    
                
            # Gestione dei representation per i request body (openapi3)
            if version == "openapi3":
            
                # Prepara elenco delle request 
                contents = method_def.get("requestBody",{}).get("content", {})
                
                # Scandisce le request previste
                for media_type, body_def in contents.items():
                                
                    schema_ref = body_def.get("schema", {}).get("$ref")
                    
                    # Se non è uno schema $ref non lo gestisce e aggiunge solo elemento WADL, altrimenti procede
                    if not schema_ref:
                        ET.SubElement(request_elem,f"{{{WADL_NAMESPACE}}}representation", mediaType=media_type)  
                    else:
                        type_name = schema_ref.split("/")[-1]

                        # Aggiunge body all'elemento WADL                                                               
                        ET.SubElement(request_elem,f"{{{WADL_NAMESPACE}}}representation", mediaType=media_type, element=f"{TARGET_PREFIX}:{request_name if ROOT_MODE!="type" else type_name}")
                        
                        # Aggiunge body all'elemento XSD
                        if not sequence:
                            request_node.set("type",f"{TARGET_PREFIX}:{type_name}")
                            if ROOT_MODE=="type": request_node.set("name", type_name)
                        else:
                           ET.SubElement(sequence,f"{{{XSD_NAMESPACE}}}element", name=request_name if ROOT_MODE!="type" else type_name, type=f"{TARGET_PREFIX}:{type_name}")                    
                                        
            # ------------------------------------------------------------------------------------------------
            # Gestisce Responses
            # ------------------------------------------------------------------------------------------------
                       
            # Gestione delle response
            for status, response in responses.items():
                
                # Se si tratta di un parametro $ref lo risolve
                response = resolve_ref_responses(response,root_responses)
                   
                # Prepara nomi per gli element di interfaccia
                response_name = operationId+"Response"+("Status"+status if status!="200" else "")
                
                # Genera elemento WADL della response
                response_elem = ET.SubElement(method,f"{{{WADL_NAMESPACE}}}response", status=status)
                
                # Prepara elenco delle response in base alla specifica
                contents = response.get("content", {}) if version == "openapi3" else {"application/json": response}
                
                # Se la response non è definita crea una representation/element vuoti, altrimenti procede
                if version == "openapi3" and contents=={}:
                                        
                    # Se è già stato aggiunto un elemento all'XSD genera eccezione
                    if response_name in element_registry:
                        print("Duplicated operation name ("+response_name+")")
                        sys.exit()
                        
                    # Aggiunge body all'elemento XSD
                    element_registry[response_name] = ET.Element(f"{{{XSD_NAMESPACE}}}element", name=response_name)                           
                    
                else:
                    
                    # Scandisce le response previste
                    for media_type, content_def in contents.items():
                    
                        schema_ref = content_def.get("schema", {}).get("$ref")
                        
                        # Se non è uno schema $ref non lo gestisce e aggiunge solo elemento WADL, altrimenti procede
                        if not schema_ref:
                            ET.SubElement(response_elem,f"{{{WADL_NAMESPACE}}}representation", mediaType=media_type)  
                        else:
                            type_name = schema_ref.split("/")[-1]
                            
                            # Aggiunge body all'elemento WADL                                                               
                            ET.SubElement(response_elem,f"{{{WADL_NAMESPACE}}}representation", mediaType=media_type, element=f"{TARGET_PREFIX}:{response_name if ROOT_MODE!="type" or status!="200" else type_name}")
                            
                            # Se è già stato aggiunto un elemento all'XSD genera eccezione
                            if response_name in element_registry:
                                print("Duplicated operation name ("+response_name+")")
                                sys.exit()
                                
                            # Aggiunge body all'elemento XSD
                            element_registry[response_name] = ET.Element(f"{{{XSD_NAMESPACE}}}element", name=response_name if ROOT_MODE!="type" or status!="200" else type_name, type=f"{TARGET_PREFIX}:{type_name}")                           

    # ================================================================================================

    return application

# ####################################################################################################
# Genera il file WSDL
# ####################################################################################################
def generate_wsdl(application, xsd_filename):

    # ================================================================================================
    # Variabili
    # ================================================================================================
    messages = []
    operations = []

    port_name = f"{SERVICE_NAME}_{SERVICE_VERSION}_Port"
    service_name = f"{SERVICE_NAME}_{SERVICE_VERSION}_Service"
    port_type_name = f"{SERVICE_NAME}_{SERVICE_VERSION}_PortType"
    binding_name = f"{SERVICE_NAME}_{SERVICE_VERSION}_Binding"

    # ================================================================================================
    # Genera Wsdl 
    # ================================================================================================
    wsdl = ET.Element(f"{{{WSDL_NAMESPACE}}}definitions", attrib={
        "name": f"{SERVICE_NAME}_{SERVICE_VERSION}",
        "targetNamespace": TARGET_NAMESPACE,
        f"xmlns:{TARGET_PREFIX}": TARGET_NAMESPACE
    })

    # ================================================================================================
    # Genera Types
    # ================================================================================================
    wsdl.append(ET.Comment("#" * 100))
    wsdl.append(ET.Comment(" TYPES "))
    wsdl.append(ET.Comment("#" * 100))    
    
    types = ET.SubElement(wsdl, f"{{{WSDL_NAMESPACE}}}types")
    schema = ET.SubElement(types, f"{{{XSD_NAMESPACE}}}schema", attrib={
        "targetNamespace": TARGET_NAMESPACE
    })
    ET.SubElement(schema, f"{{{XSD_NAMESPACE}}}include", attrib={
        "schemaLocation": os.path.basename(xsd_filename)
    })

    # ================================================================================================
    # Genera Message
    # ================================================================================================
    wsdl.append(ET.Comment("#" * 100))
    wsdl.append(ET.Comment(" MESSAGES "))
    wsdl.append(ET.Comment("#" * 100))    

    for idx, resource in enumerate(application.findall(f".//{{{WADL_NAMESPACE}}}resource")):
    
        if idx > 0:
            wsdl.append(ET.Comment(" ~~~~~~~~ "))

        path = resource.attrib.get("path", "")
        for method in resource.findall(f"{{{WADL_NAMESPACE}}}method"):
                
            # Prepara operation
            operation_name = method.attrib.get("id") 
            operation_soa = method.attrib.get(f"{{{SOA_NAMESPACE}}}wsdlOperation") 
            request_node = method.find(f"./{{{WADL_NAMESPACE}}}request/{{{WADL_NAMESPACE}}}representation[@mediaType='application/xml']")
            response_node = method.find(f"./{{{WADL_NAMESPACE}}}response[@status='200']/{{{WADL_NAMESPACE}}}representation[@mediaType='application/xml']")
            request_name = request_node.attrib.get("element") if request_node is not None else None
            response_name = response_node.attrib.get("element") if response_node is not None else None

            # Se manca operation_name lo ricava dal path estraendone l'ultimo token ignorando eventuali parametri
            if not operation_name:
                operation_name = derive_operation_id(path,method.attrib.get("name"))            
        
            # Crea i message            
            msg_in = ET.SubElement(wsdl, f"{{{WSDL_NAMESPACE}}}message", name=f"{operation_name}_InputMessage")
            ET.SubElement(msg_in, f"{{{WSDL_NAMESPACE}}}part", name="parameters", element=f"{TARGET_PREFIX}:{operation_name}Request" if not request_name else request_name)                  

            msg_out = ET.SubElement(wsdl, f"{{{WSDL_NAMESPACE}}}message", name=f"{operation_name}_OutputMessage")            
            ET.SubElement(msg_out, f"{{{WSDL_NAMESPACE}}}part", name="parameters", element=f"{TARGET_PREFIX}:{operation_name}Response" if not response_name else response_name)

            # Salva operation_name per portType/binding
            operations.append([operation_name,operation_soa])

    # ================================================================================================
    # Genera PortType
    # ================================================================================================
    wsdl.append(ET.Comment("#" * 100))
    wsdl.append(ET.Comment(" PORT TYPES "))
    wsdl.append(ET.Comment("#" * 100))        
    port_type = ET.SubElement(wsdl, f"{{{WSDL_NAMESPACE}}}portType", name=port_type_name)
    
    for idx, operation in enumerate(operations):
    
        if idx > 0:
            port_type.append(ET.Comment(" ~~~~~~~~ "))

        op = ET.SubElement(port_type, f"{{{WSDL_NAMESPACE}}}operation", name=operation[1])
        ET.SubElement(op, f"{{{WSDL_NAMESPACE}}}input", message=f"{TARGET_PREFIX}:{operation[0]}_InputMessage")
        ET.SubElement(op, f"{{{WSDL_NAMESPACE}}}output", message=f"{TARGET_PREFIX}:{operation[0]}_OutputMessage")

    # =====================
    # Genera Binding
    # =====================
    wsdl.append(ET.Comment("#" * 100))
    wsdl.append(ET.Comment(" BINDINGS "))
    wsdl.append(ET.Comment("#" * 100))    

    binding = ET.SubElement(wsdl, f"{{{WSDL_NAMESPACE}}}binding", name=binding_name, type=f"{TARGET_PREFIX}:{port_type_name}")
    ET.SubElement(binding, f"{{{SOAP_NAMESPACE}}}binding", style="document", transport="http://schemas.xmlsoap.org/soap/http")

    for idx, operation in enumerate(operations):
    
        if idx > 0:
            binding.append(ET.Comment(" ~~~~~~~~ "))

        op = ET.SubElement(binding, f"{{{WSDL_NAMESPACE}}}operation", name=operation[1])
        ET.SubElement(op, f"{{{SOAP_NAMESPACE}}}operation", soapAction=operation[1], style="document")
        ET.SubElement(op, f"{{{WSDL_NAMESPACE}}}input").append(
            ET.Element(f"{{{SOAP_NAMESPACE}}}body", use="literal")
        )
        ET.SubElement(op, f"{{{WSDL_NAMESPACE}}}output").append(
            ET.Element(f"{{{SOAP_NAMESPACE}}}body", use="literal")
        )

    # =====================
    # Genera Service
    # =====================
    wsdl.append(ET.Comment("#" * 100))
    wsdl.append(ET.Comment(" SERVICES "))
    wsdl.append(ET.Comment("#" * 100))    
    
    service = ET.SubElement(wsdl, f"{{{WSDL_NAMESPACE}}}service", name=service_name)
    port = ET.SubElement(service, f"{{{WSDL_NAMESPACE}}}port", name=port_name, binding=f"{TARGET_PREFIX}:{binding_name}")
    ET.SubElement(port, f"{{{SOAP_NAMESPACE}}}address", location="http://localhost/service")

    return wsdl

# ####################################################################################################
# Search & replace regex patterns in multiple template files
# ####################################################################################################
def batch_search_and_replace_templates(source_directory, replacements, output_basename , overwrite=True):
    """
    Esegue sostituzioni multiple nei file di una directory e salva i file aggiornati nella directory corrente.
    Il contatore numerico nel nome viene aggiunto solo se necessario per evitare conflitti.
    :param source_directory: Path della directory sorgente contenente i file originali.
    :param replacements: Lista di tuple (pattern, replacement).
    :param output_basename: Prefisso base per i file aggiornati (senza estensione).
    """
    compiled_patterns = [(re.compile(pattern), replacement) for pattern, replacement in replacements]

    for filename in os.listdir(source_directory):
    
        print("Parsing: ",filename)

        filepath = os.path.join(source_directory, filename)

        if not os.path.isfile(filepath):
            continue

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        for regex, replacement in compiled_patterns:
            content = regex.sub(replacement, content)

        filename, ext = os.path.splitext(filename)
        filename = re.sub("%FILENAME%",output_basename,filename)
        base_filename = f"{filename}{ext}"
        final_filename = base_filename
        
        if not overwrite:
            counter = 1
            while os.path.exists(final_filename):
                final_filename = f"{filename}_{counter}{ext}"
                counter += 1

        with open(final_filename, 'w', encoding='utf-8') as f:
            f.write(content)
            
        print(f"Generated: {final_filename}")

# definisce classe custom per la formattazione dell'helper degli arguments
class ArgsCustomFormatter(argparse.ArgumentDefaultsHelpFormatter):
    def __init__(self, *args, **kwargs):
        kwargs['max_help_position'] = 40
        kwargs['width'] = 150
        super().__init__(*args, **kwargs)
        
# definisce classe custom per l'uso di argomenti enumerati
class ArgsEnumAction(argparse.Action):
    def __init__(self, **kwargs):
        # Pop off the type value
        enum_type = kwargs.pop("type", None)

        # Ensure an Enum subclass is provided
        if enum_type is None:
            raise ValueError("type must be assigned an Enum when using ArgsEnumAction")
        if not issubclass(enum_type, enum.Enum):
            raise TypeError("type must be an Enum when using ArgsEnumAction")

        # Generate choices from the Enum
        kwargs.setdefault("choices", tuple(e.value for e in enum_type))

        super(ArgsEnumAction, self).__init__(**kwargs)

        self._enum = enum_type

    def __call__(self, parser, namespace, values, option_string=None):
        # Convert value back into an Enum
        value = self._enum(values)
        setattr(namespace, self.dest, value)
        
# ####################################################################################################
# Main function
# ####################################################################################################
def main():

    # dichiara variabili globali
    global OSB_PATH
    global NULL_MODE
    global ARRAY_MODE
    global ROOT_MODE
    global SERVICE_NAME
    global SERVICE_VERSION
    global TARGET_NAMESPACE
            
    # definizioni per argomenti command-line con enumerazioni
    class ArrayMode(enum.Enum):
        a = "inline"
        b = "expand"
        
    class NullMode(enum.Enum):
        a = "both"
        b = "union"
        c = "nillable"
        
    class RootMode(enum.Enum):
        a = "method"
        b = "type"
        
    # definisce argomenti a command line e ne fa il parsin
    parser = argparse.ArgumentParser(description="Convert Swagger 2.0 or OpenAPI 3.0 JSON to WADL + XSD",formatter_class=ArgsCustomFormatter)
    parser.add_argument("descriptor_file", help="Path to Swagger/OpenAPI JSON file")
    parser.add_argument("--ns", default=TARGET_NAMESPACE, help="Target namespace")
    parser.add_argument("--null-mode", default=NULL_MODE, type=NullMode, help="Null values conversion behaviour", action=ArgsEnumAction)
    parser.add_argument("--array-mode", default=ARRAY_MODE, type=ArrayMode, help="Array values conversion behaviour", action=ArgsEnumAction)
    parser.add_argument("--root-mode", default=ROOT_MODE, type=RootMode, help="Root element name generation behaviour", action=ArgsEnumAction)
    parser.add_argument("--osb-path", default="<auto-detect>", help="OSB resources path ref prefix")
    parser.add_argument("--xsd-prefix", default="XS_", help="XSD filename prefix")
    parser.add_argument("--wadl-prefix", default="WA_", help="WADL filename prefix")
    parser.add_argument("--wsdl-prefix", default="WS_", help="WSDL filename prefix")
    parser.add_argument("--wsdl-name", default=SERVICE_NAME, help="WSDL Service Name")
    parser.add_argument("--wsdl-ver", default=SERVICE_VERSION, help="WSDL Service Version")
    parser.add_argument("--file-base", default="<input-file>", help="Filename base for output files (XSD,WADL,WSDL)")
    parser.add_argument("--output-dir", default=".", help="Directory to save files")
    parser.add_argument("--templates-dir", help="Directory for search & replace templates")
    args = parser.parse_args()
    
    # aggiorna altri parametri globali in base a argomenti command-line
    NULL_MODE = args.null_mode if isinstance(args.null_mode,str) else args.null_mode.value
    ARRAY_MODE = args.array_mode if isinstance(args.array_mode,str) else args.array_mode.value
    ROOT_MODE = args.root_mode if isinstance(args.root_mode,str) else args.root_mode.value
    SERVICE_NAME = args.wsdl_name
    SERVICE_VERSION = args.wsdl_ver
    TARGET_NAMESPACE = args.ns
    
    print("")
    print("NULL_MODE:",NULL_MODE)
    print("ARRAY_MODE:",ARRAY_MODE)
    print("ROOT_MODE:",ROOT_MODE)
    print("SERVICE_NAME:",SERVICE_NAME)
    print("SERVICE_VERSION:",SERVICE_VERSION)
    print("TARGET_NAMESPACE:",TARGET_NAMESPACE)
    print("")
    
    # Se non è valorizzata inizializza la variabile di sostituzione %OSB_PATH% con i due livelli della directory corrente (<parent-dir>/<current-dir>).
    # Può essere utilizzata nei template di risorse OSB come prefisso del path delle risorse negli attributi REF che richiedono il path assoluto OSB.
    # Il default presuppone che ci si trovi in un subfolder di un progetto OSB in cui folder corrisponde a quello del progetto (<project>/<folder>).
    if args.osb_path!="<auto-detect>":
        OSB_PATH = args.osb_path
    else:
        OSB_PATH = os.path.abspath(args.output_dir)
        OSB_PATH = os.path.basename(os.path.abspath(args.output_dir+"/.."))+"/"+os.path.basename(os.path.abspath(args.output_dir))
    
    # Carica il file json del descrittore di input
    with open(args.descriptor_file, "r", encoding="utf-8") as f:
        spec = json.load(f)

    # Prepara i nomi dei file di output
    filename_base = os.path.splitext(os.path.basename(args.descriptor_file))[0] if args.file_base=="<input-file>" else args.file_base
    
    xsd_filename_base = f"{args.xsd_prefix}{filename_base}"
    xsd_filename = xsd_filename_base+".xsd"
    wadl_filename_base = f"{args.wadl_prefix}{filename_base}"
    wadl_filename = wadl_filename_base+".wadl"
    wsdl_filename_base = f"{args.wsdl_prefix}{filename_base}"
    wsdl_filename = wsdl_filename_base+".wsdl"

    # Rileva versione (Swagger/OpenApi2 o OpenApi 3)
    version = detect_version(spec)
    
    # Censisce i tipi utilizzati a vario titolo
    element_registry = {}
    nullability_registry = {}
    restriction_registry = {}

    root_schemas = extract_root_schemas(spec, version)
    root_responses = extract_root_responses(spec, version)
    root_parameters = extract_root_parameters(spec, version)
    
    # Generazione del WADL
    wadl_tree = generate_wadl(spec,version,root_responses,root_parameters,root_schemas,xsd_filename,element_registry,nullability_registry,restriction_registry)

    # Generazione del WSDL
    wsdl_tree = generate_wsdl(wadl_tree,xsd_filename)
    
    # Generazione XSD 
    xsd_tree = generate_xsd(root_schemas,element_registry,nullability_registry,restriction_registry)

    # Scrittura file XSD
    with open(os.path.join(args.output_dir, xsd_filename), "w", encoding="utf-8") as f:
        f.write(prettify_xml(xsd_tree))

    # Scrittura file WADL
    with open(os.path.join(args.output_dir, wadl_filename), "w", encoding="utf-8") as f:
        f.write(prettify_xml(wadl_tree))

    # Scrittura file WSDL
    with open(os.path.join(args.output_dir, wsdl_filename), "w", encoding="utf-8") as f:
        f.write(prettify_xml(wsdl_tree))

    print(f"Generated XSD: {xsd_filename}")
    print(f"Generated WADL: {wadl_filename}")
    print(f"Generated WSDL: {wsdl_filename}")

    # Esegue Search & Replace degli eventuali template
    replacements = [
        (r"%OSB_PATH%", OSB_PATH),
        (r"%BINDING%", f"{SERVICE_NAME}_{SERVICE_VERSION}_Binding"),
        (r"%NAMESPACE%", TARGET_NAMESPACE),
        (r"%FILENAME_BASE%", filename_base),
        (r"%XSD_FILENAME%", xsd_filename),
        (r"%WADL_FILENAME%", wadl_filename),
        (r"%WSDL_FILENAME%", wsdl_filename),
        (r"%XSD_FILENAME_BASE%", xsd_filename_base),
        (r"%WADL_FILENAME_BASE%", wadl_filename_base),
        (r"%WSDL_FILENAME_BASE%", wsdl_filename_base)
    ]
    
    # Se è definito il folder dei template ne esegue il parsing
    if args.templates_dir:
       batch_search_and_replace_templates(args.templates_dir, replacements, filename_base)    

# ####################################################################################################
# Entry point
# ####################################################################################################
if __name__ == "__main__":
    main()
