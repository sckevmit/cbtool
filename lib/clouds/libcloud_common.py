#!/usr/bin/env python

#/*******************************************************************************
# Copyright (c) 2015 DigitalOcean, Inc.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#    http://www.apache.org/licenses/LICENSE-2.0
#
#/*******************************************************************************

'''
    Created on September 8th, 2016
    Libcloud Common Library: You want to inherit from this to make it easier to
    be compatible with CloudBench
    @author: Michael R. Hines, Darrin Eden
'''
from time import time, sleep
from random import randint
from socket import gethostbyname

from lib.auxiliary.code_instrumentation import trace, cbdebug, cberr, cbwarn, cbinfo, cbcrit
from lib.auxiliary.data_ops import str2dic, DataOpsException
from lib.remote.network_functions import Nethashget

from shared_functions import CldOpsException, CommonCloudFunctions

from libcloud.compute.types import Provider
from libcloud.compute.providers import get_driver
from libcloud.compute.types import NodeState

from copy import deepcopy

import threading
import traceback

import libcloud.security

class LibcloudCmds(CommonCloudFunctions) :
    catalogs = threading.local()
    locations = False
    sizes = False
    images = False
    keys = {}

    '''
     README: Parameters:
     @description: (Required) This is the libcloud-specific identifier string for your cloud,
        as listed here under the "Provider Constant" column:
        http://libcloud.readthedocs.io/en/latest/supported_providers.html
     @num_credentials: (Required, Default 2)
             How many credentials does your cloud need to authenticate?
             For example, this might be '2' if you have both an access_token and access_key.
             For example, this might be '1' if you only have an access_token or bearer_token without a key.
             This class doesn't actually interpret your parameters, but it does use the string to interpret
             whether or not your configuration file has been setup correctly.
             Libcloud credentials must be in this format, and include a "tag" which is an arbitrary string
             that corresponds to the name of the tenant that owns the credentials.
             You can have as many credentials (tenants) as you want, but they need to be in the right format.
             Example 1)
                1. Cloud is named "FOO"
                2. There are two tenants "user1" and "user2"
                3. Each tenant only has one (1) credential for authentication: "bar" and "baz".
                4. The configuration file would look like this:
                 [USER-DEFINED : CLOUDOPTION_FOO ]
                 FOO_CREDENTIALS = user1:bar,user2:baz
             Example 2)
                1. Cloud is named "HAPPY"
                2. There are three tenants named "not", "so", and "lucky"
                3. Each tenant has the same access_key and access_token, two (2) credentials each as "foo" and "bar"
                4. The configuration file would look like this:
                 [USER-DEFINED : CLOUDOPTION_HAPPY ]
                 HAPPY_CREDENTIALS = not:foo:bar,so:foo:bar,lucky:foo:bar
            In Example 1), @num_credentials = 1
            In Example 2), @num_credentials = 2
            By, default we assume Oauth-driven clouds that have both an access token and an access key.
            If you have more or less than that, please set the value accordingly.
     @use_ssh_keys: (Optional for cloud-init-based clouds, Default false)
            A comma/colon-separated list of ssh key identifiers that can be looked up by libcloud and installed by cloud-init.
            For example:
                1. Cloud is named "FOO"
                2. two SSH keys, identified by "a" and "b"
                3. The configuration file would look like this:
                 [USER-DEFINED : CLOUDOPTION_FOO ]
                 FOO_KEY_NAME = a,b # identifiers used by cloud-init to install the public key
                 FOO_SSH_KEY_NAME = path/to/private/key/for/cbtool
            Using libcloud, we will then go and lookup those keys and use them at instance creation time.
            NOTE: FOO_SSH_KEY_NAME != FOO_SSH_KEY_NAME. The first one is the private key used by cbtool,
                  and the second one is the public key.
            NOTE: If your cloud doesn't support cloud-init, then you will need to make sure the VM image is prepared in advance
                  with the public key that corresponds to FOO_SSH_KEY_NAME
    @use_cloud_init: (Optional, Default False)
            NOTE: If your cloud doesn't support cloud-init, you will need to make sure your images have baked in the SSH public key on your own.
    @use_volumes: (Optional, Default False)
            Leave this as false if you're not interested in block storage / volume support, or you're cloud doesn't support it.
    @use_sizes: (Optional, Default True)
            Use the VM image sizes as listed by your cloud.
    @use_locations: (Optional, Default True)
            Use the Regions as listed by your cloud.
    @verify_ssl: (Optional, Default True)
            Whether or not to have libcloud verify SSL certificates when communicating with the cloud
    @tldomain: (Optional, Default False)
            The FQDN of your cloud. None if false.
    @extra: (Optional, Default empty)
            Extra fixed parameters to be used by libcloud that we don't know about, which get passed at instance creation time.
    '''

    @trace
    def __init__ (self, pid, osci, expid = None, provider = "OverrideMe", \
                  num_credentials = 2, use_ssh_keys = False, \
                  use_cloud_init = False,  use_volumes = False, use_locations = True, \
                  use_sizes = True, tldomain = False, verify_ssl = True, extra = {}) :
        '''
        TBD
        '''
        CommonCloudFunctions.__init__(self, pid, osci, expid)
        self.access_url = False
        self.ft_supported = False
        self.current_token = 0
        self.cache_mutex = threading.Lock()
        self.token_mutex = threading.Lock()
        self.provider = provider
        self.num_credentials = num_credentials
        self.use_ssh_keys = use_ssh_keys
        self.use_cloud_init = use_cloud_init
        self.use_volumes = use_volumes
        self.use_locations = use_locations
        self.use_sizes = use_sizes
        self.tldomain = tldomain
        self.extra = extra
        self.additional_rc_contents = ''        
        self.extra["kwargs"] = {}
        self.access = False

        libcloud.security.VERIFY_SSL_CERT = verify_ssl

    @trace
    def get_description(self) :
        '''
        TBD
        '''
        return "LibCloud Base"

    @trace
    def connect(self, credentials_list, obj_attr_list = False) :
        
        if not self.access and obj_attr_list and "access" in obj_attr_list :
            self.access = obj_attr_list["access"]

        credentials = credentials_list.split(":")
        if len(credentials) != (self.num_credentials + 1) :
            raise CldOpsException(self.get_description() + " needs at least " + str(self.num_credentials) + " credentials, including an arbitrary tag representing the tenant. Refer to the templates for examples.", 8499)

        tenant = credentials[0]

        # libcloud is totally not thread-safe. bastards.
        cbdebug("Checking libcloud connection...")
        try :
            getattr(LibcloudCmds.catalogs, "cbtool")
        except AttributeError, e :
            cbdebug("Initializing thread local connection: ")

            LibcloudCmds.catalogs.cbtool = {}

        self.cache_mutex.acquire()
        _hostname = "NA"        
        try :
            _status = 100

            if credentials_list not in LibcloudCmds.catalogs.cbtool :
                cbdebug("Connecting to " + self.get_description() + "...")
                _status = 110
                driver = self.get_real_driver(self.provider)
                LibcloudCmds.catalogs.cbtool[credentials_list] = self.get_libcloud_driver(driver, tenant, *credentials[1:])
            else :
                cbdebug(self.get_description()  + " Already connected.")

            cbdebug("Caching " + self.get_description() + " locations. If stale, then restart...")

            if self.use_locations :
                if not LibcloudCmds.locations :
                    cbdebug("Caching " + self.get_description()  + " Locations...", True)
                    LibcloudCmds.locations = LibcloudCmds.catalogs.cbtool[credentials_list].list_locations()

                    if obj_attr_list and "name" in obj_attr_list :
                        _hostname = obj_attr_list["name"]                    
                    
                assert(LibcloudCmds.locations)

            if self.use_sizes :
                if not LibcloudCmds.sizes :
                    cbdebug("Caching " + self.get_description() + " Sizes...", True)
                    LibcloudCmds.sizes = LibcloudCmds.catalogs.cbtool[credentials_list].list_sizes()
                    assert(LibcloudCmds.sizes)

            if self.use_ssh_keys :
                if credentials_list not in LibcloudCmds.keys :
                    cbdebug("Caching " + self.get_description() + " keys (" + tenant + ")", True)
                    LibcloudCmds.keys[credentials_list] = LibcloudCmds.catalogs.cbtool[credentials_list].list_key_pairs()
                    assert(credentials_list in LibcloudCmds.keys)

            cbdebug("Done caching.")

            _status = 0

        except Exception, e:
            _msg = "Error connecting " + self.get_description() + ": " + str(e)
            cbdebug(_msg, True)
            _status = 23
            
        finally :
            self.cache_mutex.release()
            if _status :
                _msg = self.get_description() + " connection failure. Failed to use your credentials for account: " + tenant
                cbdebug(_msg, True)
                cberr(_msg)
                if credentials_list in LibcloudCmds.catalogs.cbtool :
                    del LibcloudCmds.catalogs.cbtool[credentials_list]
                raise CldOpsException(_msg, _status)
            else :
                _msg = self.get_description() + " connection successful."
                cbdebug(_msg)
                return _status, _msg, LibcloudCmds.catalogs.cbtool[credentials_list], _hostname

    @trace
    def test_vmc_connection(self, cloud_name, vmc_name, access, credentials, key_name, \
                            security_group_name, vm_templates, vm_defaults, vmc_defaults) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            for credentials_list in credentials.split(","):
                _status, _msg, _local_conn, _hostname = self.connect(credentials_list, vmc_defaults)

            self.generate_rc(cloud_name, vmc_defaults, self.additional_rc_contents)

            _prov_netname_found, _run_netname_found = self.check_networks(vmc_name, vm_defaults)
            
            _key_pair_found = self.check_ssh_key(vmc_name, self.determine_key_name(vm_defaults), vm_defaults, False, _local_conn)
            
            _detected_imageids = self.check_images(vmc_name, vm_templates, _local_conn)

            if not (_run_netname_found and _prov_netname_found and _key_pair_found) :
                _msg = "Check the previous errors, fix it (using " + self.get_description()
                _msg += "'s CLI or (Web) GUI"
                _status = 1178
                raise CldOpsException(_msg, _status) 

            if len(_detected_imageids) :
                _status = 0               
            else :
                _status = 1

        except CldOpsException, obj :
            _fmsg = str(obj.msg)
            _status = 2

        except Exception, msg :
            _fmsg = str(msg)
            _status = 23

        finally :
            _status, _msg = self.common_messages("VMC", {"name" : vmc_name }, "connected", _status, _fmsg)
            return _status, _msg

    @trace
    def check_networks(self, vmc_name, vm_defaults) :
        '''
        TBD
        '''
        _prov_netname = vm_defaults["netname"]
        _run_netname = vm_defaults["netname"]

        _prov_netname_found = True
        _run_netname_found = True
            
        return _prov_netname_found, _run_netname_found

    @trace
    def check_images(self, vmc_name, vm_templates, connection) :
        '''
        TBD
        '''
        self.common_messages("IMG", { "name": vmc_name }, "checking", 0, '')

        _registered_image_list = connection.list_images()

        _registered_imageid_list = []

        _map_name_to_id = {}
        _map_id_to_name = {}

        for _registered_image in _registered_image_list :
            _registered_imageid_list.append(_registered_image.id)
            _map_name_to_id[str(_registered_image.name.encode('utf-8').strip())] = str(_registered_image.id)
            _map_name_to_id[str(_registered_image.id.encode('utf-8').strip())] = str(_registered_image.id)

        for _vm_role in vm_templates.keys() :            
            _imageid = str2dic(vm_templates[_vm_role])["imageid1"]
            if _imageid != "to_replace" :
                if _imageid in _map_name_to_id :                     
                    vm_templates[_vm_role] = vm_templates[_vm_role].replace(_imageid, _map_name_to_id[_imageid])
                else :
                    _map_name_to_id[_imageid] = '00' + ''.join(["%s" % randint(0, 9) for num in range(0, 5)]) + '0'
                    vm_templates[_vm_role] = vm_templates[_vm_role].replace(_imageid, _map_name_to_id[_imageid])                        

                _map_id_to_name[_map_name_to_id[_imageid]] = _imageid

        _detected_imageids = self.base_check_images(vmc_name, vm_templates, _registered_imageid_list, _map_id_to_name)

        return _detected_imageids

    @trace
    def discover_hosts(self, obj_attr_list, start) :
        '''
        TBD
        '''
        _host_uuid = obj_attr_list["cloud_vm_uuid"]

        obj_attr_list["host_list"] = {}
        obj_attr_list["hosts"] = ''

        obj_attr_list["initial_hosts"] = ''.split(',')
        obj_attr_list["host_count"] = len(obj_attr_list["initial_hosts"])
    
        return True

    @trace
    def vmccleanup(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            for credentials_list in obj_attr_list["credentials"].split(","):
                _status, _msg, _local_conn, _hostname = self.connect(credentials_list)
    
            _running_instances = True
            while _running_instances :
                _running_instances = False
                for credentials_list in obj_attr_list["credentials"].split(","):
                    credentials = credentials_list.split(":")
                    tenant = credentials[0]
                    obj_attr_list["tenant"] = tenant
                    self.common_messages("VMC", obj_attr_list, "cleaning up vms", 0, '')

                    _reservations = LibcloudCmds.catalogs.cbtool[credentials_list].list_nodes()

                    for _reservation in _reservations :
                        if _reservation.name.count("cb-" + obj_attr_list["username"] + "-" + obj_attr_list["cloud_name"]) :
                            if _reservation.state == NodeState.PENDING :
                                cbdebug("Instance still has a pending event. waiting to destroy...")
                                sleep(10)
                                _msg = "Cleaning up " + self.get_description() + ". Destroying CB instantiated node: " + _reservation.name
                                cbdebug(_msg)
                                continue

                            try :
                                cbdebug("Killing: " + _reservation.name + " (" + tenant + ")", True)
                                _reservation.destroy()
                            except :
                                pass
                            _running_instances = True
                        else :
                            _msg = "Cleaning up " + self.get_description() + ".  Ignoring instance: " + _reservation.name
                            cbdebug(_msg)

                    if _running_instances :
                        sleep(int(obj_attr_list["update_frequency"]))

            if self.use_volumes :    
                _running_volumes = True
                while _running_volumes :
                    _running_volumes = False
                    for credentials_list in obj_attr_list["credentials"].split(","):
                        credentials = credentials_list.split(":")
                        tenant = credentials[0]
                        self.common_messages("VMC", obj_attr_list, "cleaning up vvs", 0, '')
                        obj_attr_list["tenant"] = tenant
    
                        _volumes = LibcloudCmds.catalogs.cbtool[credentials_list].list_volumes()
                        for _volume in _volumes :
                            if _volume.name.count("cb-" + obj_attr_list["username"] + "-" + obj_attr_list["cloud_name"].lower()) :
                                try :
                                    cbdebug("Destroying: " + _volume.name + " (" + tenant + ")", True)
                                    _volume.destroy()
                                except :
                                    pass
                                _running_volumes = True
                            else :
                                _msg = "Cleaning up " + self.get_description() + ". Ignoring volume: " + _volume.name
                                cbdebug(_msg)
    
                        if _running_volumes :
                            sleep(int(obj_attr_list["update_frequency"]))

            _status = 0
            
        except CldOpsException, obj :
            _fmsg = str(obj.msg)
            cberr(_msg)
            _status = 2

        except Exception, msg :
            _fmsg = str(msg)
            _status = 23
    
        finally :
            _status, _msg = self.common_messages("VMC", obj_attr_list, "cleaned up", _status, _fmsg)
            return _status, _msg

    @trace
    def vmcregister(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            _time_mark_prs = int(time())
            obj_attr_list["mgt_002_provisioning_request_sent"] = _time_mark_prs - int(obj_attr_list["mgt_001_provisioning_request_originated"])
            
            if "cleanup_on_attach" in obj_attr_list and obj_attr_list["cleanup_on_attach"] == "True" :             
                _status, _fmsg = self.vmccleanup(obj_attr_list)
            else :
                _status = 0

            for credentials_list in obj_attr_list["credentials"].split(","):                
                _x, _y, _z, _hostname = self.connect(credentials_list, obj_attr_list)
            
            obj_attr_list["cloud_hostname"] = _hostname
            obj_attr_list["cloud_ip"] = gethostbyname(self.tldomain)
            obj_attr_list["arrival"] = int(time())

            if str(obj_attr_list["discover_hosts"]).lower() == "true" :
                self.discover_hosts(obj_attr_list, _time_mark_prs)
            else :
                obj_attr_list["hosts"] = ''
                obj_attr_list["host_list"] = {}
                obj_attr_list["host_count"] = "NA"

            _time_mark_prc = int(time())

            obj_attr_list["mgt_003_provisioning_request_completed"] = _time_mark_prc - _time_mark_prs

            _status = 0
            
        except CldOpsException, obj :
            _fmsg = str(obj.msg)
            _status = 2

        except Exception, msg :
            _fmsg = str(msg)
            _status = 23

        finally :
            _status, _msg = self.common_messages("VMC", obj_attr_list, "registered", _status, _fmsg)
            return _status, _msg

    @trace
    def vmcunregister(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            _time_mark_drs = int(time())

            if "mgt_901_deprovisioning_request_originated" not in obj_attr_list :
                obj_attr_list["mgt_901_deprovisioning_request_originated"] = _time_mark_drs

            obj_attr_list["mgt_902_deprovisioning_request_sent"] = _time_mark_drs - int(obj_attr_list["mgt_901_deprovisioning_request_originated"])
            
            if "cleanup_on_detach" in obj_attr_list and obj_attr_list["cleanup_on_detach"] == "True" :
                _status, _fmsg = self.vmccleanup(obj_attr_list)

            _time_mark_prc = int(time())
            obj_attr_list["mgt_903_deprovisioning_request_completed"] = _time_mark_prc - _time_mark_drs
            
            _status = 0
            
        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, msg :
            _fmsg = str(msg)
            _status = 23
    
        finally :
            _status, _msg = self.common_messages("VMC", obj_attr_list, "unregistered", _status, _fmsg)
            return _status, _msg

    @trace
    def vmcount(self, obj_attr_list):
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"                        
            _nr_instances = 0

            for _vmc_uuid in self.osci.get_object_list(obj_attr_list["cloud_name"], "VMC") :
                _vmc_attr_list = self.osci.get_object(obj_attr_list["cloud_name"], \
                                                      "VMC", False, _vmc_uuid, \
                                                      False)
                
                for credentials_list in _vmc_attr_list["credentials"].split(","):
                    _status, _msg, _local_conn, _hostname = self.connect(credentials_list)
                    
                    _instance_list = _local_conn.list_nodes()
                    
                    if _instance_list :
                        for _instance in _instance_list :
                            if _instance.name.count("cb-" + obj_attr_list["username"] + '-' + obj_attr_list["cloud_name"]) :
                                _nr_instances += 1

        except Exception, e :
            _status = 23
            _nr_instances = "NA"
            _fmsg = "(While counting instance(s) through API call \"list\") " + str(e)

        finally :
            return _nr_instances

    @trace
    def get_ip_address(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            node = self.get_instances(obj_attr_list)

            if len(node.private_ips) > 0 and obj_attr_list["run_netname"].lower() == "private" :
                obj_attr_list["run_cloud_ip"] = node.private_ips[0]
            else :
                if len(node.public_ips) > 0 :
                    obj_attr_list["run_cloud_ip"] = node.public_ips[0]
                else :
                    cbdebug("Instance Public address not yet available.")
                    return False

            # NOTE: "cloud_ip" is always equal to "run_cloud_ip"
            obj_attr_list["cloud_ip"] = obj_attr_list["run_cloud_ip"]

            if obj_attr_list["hostname_key"] == "cloud_vm_name" :
                obj_attr_list["cloud_hostname"] = obj_attr_list["cloud_vm_name"]
            elif obj_attr_list["hostname_key"] == "cloud_ip" :
                obj_attr_list["cloud_hostname"] = obj_attr_list["cloud_ip"].replace('.','-')

            _msg = "Public IP = " + str(node.public_ips)
            _msg += " Private IP = " + str(node.private_ips)
            cbdebug(_msg)

            if str(obj_attr_list["use_vpn_ip"]).lower() == "true" and str(obj_attr_list["vpn_only"]).lower() == "true" :
                assert(self.get_attr_from_pending(obj_attr_list))

                if "cloud_init_vpn" not in obj_attr_list :
                    cbdebug("Instance VPN address not yet available.")
                    return False
                cbdebug("Found VPN IP: " + obj_attr_list["cloud_init_vpn"])
                obj_attr_list["prov_cloud_ip"] = obj_attr_list["cloud_init_vpn"]
            else :
                if len(node.private_ips) > 0 and obj_attr_list["prov_netname"].lower() == "private" :
                    obj_attr_list["prov_cloud_ip"] = node.private_ips[0]
                else :
                    obj_attr_list["prov_cloud_ip"] = node.public_ips[0]

            _status = 0
            return True

        except Exception, e :
            _msg = "Could not retrieve IP addresses for object " + obj_attr_list["uuid"]
            _msg += " from " + self.get_description() + " \"" + obj_attr_list["cloud_name"] + ": " + str(e)
            cberr(_msg)
            raise CldOpsException(_msg, _status)

    @trace
    def get_instances(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _msg = "cloud_vm_name " + obj_attr_list["cloud_vm_name"]
            _msg += " from " + self.get_description() + " \"" + obj_attr_list["cloud_name"]
            cbdebug(_msg)

            node_list = LibcloudCmds.catalogs.cbtool[obj_attr_list["credentials_list"]].list_nodes()

            node = False
            if node_list :
                for x in node_list :
                    if x.name == obj_attr_list["cloud_vm_name"] :
                        node = x
                        break
            _status = 0

            return node

        except Exception, e :
            _status = 23
            _fmsg = str(e)
            raise CldOpsException(_fmsg, _status)

    @trace
    def get_images(self, obj_attr_list, fail = True) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"                        

            _candidate_images = False
            
            if self.is_cloud_image_uuid(obj_attr_list["imageid1"]) :
                _candidate_images = LibcloudCmds.catalogs.cbtool[obj_attr_list["credentials_list"]].get_image(obj_attr_list["imageid1"])
            else :
                for _image in LibcloudCmds.catalogs.cbtool[obj_attr_list["credentials_list"]].list_images() :
                    if _image.name == obj_attr_list["imageid1"] :
                        _candidate_images = _image
                        break

            _fmsg = "Please check if the defined image name is present on this "
            _fmsg += self.get_description()

            if _candidate_images :
                obj_attr_list["imageid1"] = _candidate_images.name
                obj_attr_list["boot_volume_imageid1"] = _candidate_images.id
                _status = 0

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                if fail :
                    _msg = "Image Name (" +  obj_attr_list["imageid1"] + ") not found: " + _fmsg
                    cberr(_msg)
                    raise CldOpsException(_msg, _status)
                else :
                    return False
            else :
                return _candidate_images

    @trace
    def get_networks(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            _status = 0

        except Exception, e :
            _status = 23
            _fmsg = str(e)
            
        finally :
            if _status :
                _msg = "Network (" +  obj_attr_list["prov_netname"] + " ) not found: " + _fmsg
                cberr(_msg, True)
                raise CldOpsException(_msg, _status)
            else :
                return True

    @trace
    def is_vm_running(self, obj_attr_list):
        '''
        TBD
        '''
        try :
            node = self.get_instances(obj_attr_list)
            return node and node.state == NodeState.RUNNING

        except Exception, e :
            _status = 23
            _fmsg = str(e)
            raise CldOpsException(_fmsg, _status)

    @trace
    def is_vm_ready(self, obj_attr_list) :
        '''
        TBD
        '''
        if self.is_vm_running(obj_attr_list) :

            if self.get_ip_address(obj_attr_list) :
                obj_attr_list["last_known_state"] = "running with ip assigned"
                return True
            else :
                obj_attr_list["last_known_state"] = "running with ip unassigned"
                return False
        else :
            obj_attr_list["last_known_state"] = "not running"
            return False

    @trace
    def vm_placement(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            _status = 0

        except Exception, e :
            _status = 23
            _fmsg = str(e)
            
        finally :
            if _status :
                _msg = "VM placement failed: " + _fmsg
                cberr(_msg, True)
                raise CldOpsException(_msg, _status)
            else :
                return True

    @trace
    def vvcreate(self, obj_attr_list, connection) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            obj_attr_list["cloud_vv_instance"] = None

            if "cloud_vv_type" not in obj_attr_list :

                if self.use_volumes :                
                    obj_attr_list["cloud_vv_type"] = "LCV"
                else :
                    obj_attr_list["cloud_vv_type"] = "NOT SUPPORTED"

            if self.use_volumes and "cloud_vv" in obj_attr_list and str(obj_attr_list["cloud_vv"]).lower() != "false" :

                obj_attr_list["region"] = _region = obj_attr_list["vmc_name"]
                obj_attr_list["cloud_vv_name"] = obj_attr_list["cloud_vv_name"].lower().replace("_", "-")
                
                obj_attr_list["last_known_state"] = "about to send volume create request"

                self.common_messages("VV", obj_attr_list, "creating", _status, _fmsg)

                if self.use_volumes :
                    
                    _mark1 = int(time())
    
                    _volume = connection.create_volume(int(obj_attr_list["cloud_vv"]),
                                                      obj_attr_list["cloud_vv_name"],
                                                      location = [x for x in LibcloudCmds.locations if x.id == obj_attr_list["region"]][0])
    
                    sleep(int(obj_attr_list["update_frequency"]))
    
                    obj_attr_list["cloud_vv_uuid"] = _volume.id
                    obj_attr_list["cloud_vv_instance"] = _volume
    
                    _mark2 = int(time())
                    obj_attr_list["do_015_create_volume_time"] = _mark2 - _mark1

                else :
                    obj_attr_list["cloud_vv_uuid"] = "NOT SUPPORTED"

            _status = 0

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)
    
        finally :                
            _status, _msg = self.common_messages("VV", obj_attr_list, "created", _status, _fmsg)
            return _status, _msg

    @trace        
    def vvdestroy(self, obj_attr_list, connection) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            if str(obj_attr_list["cloud_vv_uuid"]).lower() != "not supported" and str(obj_attr_list["cloud_vv_uuid"]).lower() != "none" :    
                self.common_messages("VV", obj_attr_list, "destroying", 0, '')

                _volumes = connection.list_volumes()
                for _volume in _volumes :
                    if _volume.name == obj_attr_list["cloud_vv_name"] :
                        try :
                            _volume.destroy()
                            break
                        except :
                            pass
                                
            _status = 0

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)
    
        finally :                
            _status, _msg = self.common_messages("VV", obj_attr_list, "destroyed", _status, _fmsg)
            return _status, _msg

    @trace
    def vmcreate(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred when creating new Droplet, but no error message was captured"
            _instance = False
            volume = False
            extra = deepcopy(self.extra)
            
            # This is just to pass regression tests.
            test_map = dict(platinum64 = "64gb", rhodium64 = "48gb", \
                            gold64 = "32gb", silver64 = "16gb", bronze64 = "8gb",\
                             copper64 = "4gb", gold32 = "4gb", silver32 = "4gb",\
                              iron64 = "2gb", iron32 = "2gb", bronze32 = "2gb",\
                               copper32 = "2gb", micro32 = "1gb", nano32 = "1gb",\
                                pico32 = "512mb")
            
            _requested_size = obj_attr_list["size"]

            if _requested_size in test_map :
                _requested_size = test_map[_requested_size]

            self.determine_instance_name(obj_attr_list)            
            self.determine_key_name(obj_attr_list)
            
            obj_attr_list["last_known_state"] = "about to connect to " + self.get_description() + " manager"

            self.take_action_if_requested("VM", obj_attr_list, "provision_originated")

            if obj_attr_list["ai"] != "none" :
                _credentials_list = self.osci.pending_object_get(obj_attr_list["cloud_name"], "AI", obj_attr_list["ai"], "credentials_list")
            else :
                _credentials_list = self.rotate_token(obj_attr_list["cloud_name"])

            obj_attr_list["tenant"] = _credentials_list.split(":")[0]
            obj_attr_list["credential"] = _credentials_list.split(":")[1]
            obj_attr_list["credentials_list"] = _credentials_list

            self.connect(_credentials_list, obj_attr_list)
            
            if self.is_vm_running(obj_attr_list) :
                _msg = "An instance named \"" + obj_attr_list["cloud_vm_name"]
                _msg += "\" is already running. It needs to be destroyed first."
                _status = 187
                cberr(_msg)
                raise CldOpsException(_msg, _status)

            _time_mark_prs = int(time())
            obj_attr_list["mgt_002_provisioning_request_sent"] = _time_mark_prs - int(obj_attr_list["mgt_001_provisioning_request_originated"])

            self.pre_vmcreate_process(obj_attr_list)
            self.vm_placement(obj_attr_list)

            obj_attr_list["last_known_state"] = "about to send create request"

            _image = self.get_images(obj_attr_list)
            self.get_networks(obj_attr_list)

            obj_attr_list["config_drive"] = False

            _status, _fmsg = self.vvcreate(obj_attr_list, LibcloudCmds.catalogs.cbtool[_credentials_list])

            self.common_messages("VM", obj_attr_list, "creating", 0, '')

            if self.use_ssh_keys :

                keys = []
    
                tmp_keys = obj_attr_list["key_name"].split(",")
                for dontcare in range(0, 2) :
                    for tmp_key in tmp_keys :
                        for key in LibcloudCmds.keys[_credentials_list] :
                            if tmp_key in [key.name, key.extra["id"]] and key.extra["id"] not in keys and key.name not in keys :
                                keys.append(key.extra["id"])
    
                    if len(keys) >= len(tmp_keys) :
                        break
    
                    cbdebug("Only found " + str(len(keys)) + " keys. Refreshing key list...", True)
                    LibcloudCmds.keys[_credentials_list] = LibcloudCmds.catalogs.cbtool[_credentials_list].list_key_pairs()

                extra["ssh_keys"] = keys

                if len(keys) != len(tmp_keys) :
                    raise CldOpsException("Not all SSH keys exist. Check your configuration: " + obj_attr_list["key_name"], _status, True)

            # Currently, regions and VMCs are the same in libcloud based adapters
            obj_attr_list["region"] = _region = obj_attr_list["vmc_name"]
            extra.update(self.pre_vmcreate(obj_attr_list, extra))

            if self.use_locations :
                _location = [x for x in LibcloudCmds.locations if x.id == obj_attr_list["region"]][0]
            else :
                _location = False 

            if self.use_sizes :
                _size = [x for x in LibcloudCmds.sizes if x.id == _requested_size][0]
            else :
                _size = False

            kwargs = deepcopy(extra["kwargs"])
            del extra["kwargs"]

            _reservation = LibcloudCmds.catalogs.cbtool[_credentials_list].create_node(
                image = _image,
                name = obj_attr_list["cloud_vm_name"],
                size = _size, 
                location = _location,
                ex_user_data = self.populate_cloudconfig(obj_attr_list) if self.use_cloud_init else False,
                ex_create_attr = extra,
                **kwargs
                )

            if "image" in obj_attr_list :
                del obj_attr_list["image"]

            obj_attr_list["last_known_state"] = "sent create request"

            if _reservation :
                
                cbdebug("ID: " + str(_reservation.id), True)
                obj_attr_list["last_known_state"] = "vm created"
                sleep(int(obj_attr_list["update_frequency"]))

                obj_attr_list["cloud_vm_uuid"] = _reservation.uuid

                self.take_action_if_requested("VM", obj_attr_list, "provision_started")

                _time_mark_prc = self.wait_for_instance_ready(obj_attr_list, _time_mark_prs)

                if obj_attr_list["cloud_vv_instance"] :

                    if not obj_attr_list["cloud_vv_instance"].attach(_reservation) :
                        _fmsg = "Volume attach failed. Aborting VM creation..."
                        obj_attr_list["cloud_vv_instance"].destroy()
                        raise CldOpsException(_fmsg, _status)

                self.wait_for_instance_boot(obj_attr_list, _time_mark_prc)
                obj_attr_list["host_name"] = _reservation.id

                _status = 0

                if obj_attr_list["force_failure"].lower() == "true" :
                    _fmsg = "Forced failure (option FORCE_FAILURE set \"true\")"                    
                    _status = 916                
                
            else :
                obj_attr_list["host_name"] = "unknown"
                obj_attr_list["last_known_state"] = "vm creation failed"
                _fmsg = "Failed to obtain instance's (cloud-assigned) uuid. The "
                _fmsg += "instance creation failed for some unknown reason."
                cberr(_fmsg)
                _status = 100

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)
            cbwarn("Error during reservation creation: " + _fmsg)

        except Exception, e :
            for line in traceback.format_exc().splitlines() :
                cbwarn(line, True)
            _status = 23
            _fmsg = str(e)
            cbwarn("Error reaching " + self.get_description() + ":" + _fmsg)

        finally :
            if not _status :
                if "instance_obj" in obj_attr_list :
                    del obj_attr_list["instance_obj"]
    
                if obj_attr_list["cloud_vv_instance"] :
                    del obj_attr_list["cloud_vv_instance"]
               
            _status, _msg = self.common_messages("VM", obj_attr_list, "created", _status, _fmsg)
            return _status, _msg

    @trace
    def vmdestroy(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            cbdebug("Last known state: " + str(obj_attr_list["last_known_state"]))

            _wait = int(obj_attr_list["update_frequency"])
            _curr_tries = 0
            _max_tries = int(obj_attr_list["update_attempts"])

            _credentials_list = obj_attr_list["credentials_list"]
            self.connect(_credentials_list)

            if "instance_obj" in obj_attr_list :
                if obj_attr_list["instance_obj"] :

                    self.common_messages("VM", obj_attr_list, "destroying", 0, '')                    

                    _time_mark_drs = int(time())

                    if "mgt_901_deprovisioning_request_originated" not in obj_attr_list :
                        obj_attr_list["mgt_901_deprovisioning_request_originated"] = _time_mark_drs
                                        
                    obj_attr_list["instance_obj"].destroy()
                    
                    sleep(_wait)
                    del obj_attr_list["instance_obj"]

                if obj_attr_list["cloud_vv_instance"] :
                    obj_attr_list["cloud_vv_instance"].destroy()
                    del obj_attr_list["cloud_vv_instance"]

                obj_attr_list["mgt_902_deprovisioning_request_sent"] = int(time()) - int(obj_attr_list["mgt_901_deprovisioning_request_originated"])
            
            else :
    
                self.common_messages("VM", obj_attr_list, "destroying", 0, '')
    
                firsttime = True
                _time_mark_drs = int(time())
                _instance = self.get_instances(obj_attr_list)
                while _instance and _curr_tries < _max_tries :
                    _errmsg = "get_instances"
                    cbdebug("Getting instance...")
                    _instance = self.get_instances(obj_attr_list)
                    if not _instance :
                        cbdebug("Breaking...")
                        if firsttime :
                            if "mgt_901_deprovisioning_request_originated" not in obj_attr_list :
                                obj_attr_list["mgt_901_deprovisioning_request_originated"] = _time_mark_drs
                        break
    
                    if _instance.state == NodeState.PENDING :
                        try :
                            _instance.destroy()
                        except :
                            pass
                        cbdebug(self.get_description() + " still has a pending event. Waiting to destroy...", True)
                        sleep(_wait)
                        continue
    
                    try :
                        if firsttime :
                            if "mgt_901_deprovisioning_request_originated" not in obj_attr_list :
                                obj_attr_list["mgt_901_deprovisioning_request_originated"] = _time_mark_drs
    
                        _instance.destroy()
    
                        if firsttime :
                            obj_attr_list["mgt_902_deprovisioning_request_sent"] = int(time()) - int(obj_attr_list["mgt_901_deprovisioning_request_originated"])
    
                        firsttime = False
                    except :
                        pass
    
                    _msg = "Inside destroy. " + _errmsg
                    _msg += " after " + str(_curr_tries) + " attempts. Will retry in " + str(_wait) + " seconds."
                    cbdebug(_msg)
                    sleep(_wait)
                    _curr_tries += 1                    
                    cbdebug("Next try...")
            
            _time_mark_drc = int(time())
            obj_attr_list["mgt_903_deprovisioning_request_completed"] = _time_mark_drc - _time_mark_drs

            _status, _fmsg = self.vvdestroy(obj_attr_list, LibcloudCmds.catalogs.cbtool[_credentials_list])

            obj_attr_list["last_known_state"] = "vm destoyed"
            self.take_action_if_requested("VM", obj_attr_list, "deprovision_finished")

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, msg :
            _fmsg = str(msg)
            _status = 23
    
        finally :
            _status, _msg = self.common_messages("VM", obj_attr_list, "destroyed", _status, _fmsg)
            return _status, _msg

    @trace
    def vmcapture(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            _wait = int(obj_attr_list["update_frequency"])
            _curr_tries = 0
            _max_tries = int(obj_attr_list["update_attempts"])

            _credentials_list = obj_attr_list["credentials_list"]
            self.connect(_credentials_list)
            
            _instance = self.get_instances(obj_attr_list)

            if _instance :

                _time_mark_crs = int(time())

                # Just in case the instance does not exist, make crc = crs
                _time_mark_crc = _time_mark_crs

                obj_attr_list["mgt_102_capture_request_sent"] = _time_mark_crs - obj_attr_list["mgt_101_capture_request_originated"]

                if obj_attr_list["captured_image_name"] == "auto" :
                    obj_attr_list["captured_image_name"] = obj_attr_list["image"] + "_captured_at_"
                    obj_attr_list["captured_image_name"] += str(obj_attr_list["mgt_101_capture_request_originated"])

                self.common_messages("VM", obj_attr_list, "capturing", 0, '')

                LibcloudCmds.catalogs.cbtool[_credentials_list].create_image(_instance, obj_attr_list["captured_image_name"])

                _capture_image_id = None
                _vm_image_created = False
                _image_instance = False
                
                while not _vm_image_created and _curr_tries < _max_tries :

                    if not _capture_image_id :
                        for _image in LibcloudCmds.catalogs.cbtool[_credentials_list].list_images() :
                            if _image.name == obj_attr_list["captured_image_name"] :
                                _image_instance = _image
                                _capture_image_id = _image.id
                                break
                    else :
                        _image_instance = LibcloudCmds.catalogs.cbtool[_credentials_list].get_image(_capture_image_id)

                    if _image_instance  :
                        _vm_image_created = True
                        _time_mark_crc = int(time())
                        obj_attr_list["mgt_103_capture_request_completed"] = _time_mark_crc - _time_mark_crs
                        break

                    sleep(int(obj_attr_list["update_frequency"]))
                    _curr_tries += 1

            else :
                _fmsg = "This instance does not exist"
                _status = 1098

            if _curr_tries > _max_tries  :
                _status = 1077
                _fmsg = "" + obj_attr_list["name"] + ""
                _fmsg += " (cloud-assigned uuid " + obj_attr_list["cloud_vm_uuid"] + ") "
                _fmsg +=  "could not be captured after " + str(_max_tries * _wait) + " seconds.... "
            else :
                _status = 0

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            _status, _msg = self.common_messages("VM", obj_attr_list, "captured", _status, _fmsg)
            return _status, _msg

    def vmrunstate_do(self, instance, credentials_list, obj_attr_list) :
        _ts = obj_attr_list["target_state"]
        _cs = obj_attr_list["current_state"]

        if instance :
            if _ts == "fail" :
                LibcloudCmds.catalogs.cbtool[credentials_list].ex_shutdown_node(instance)
            elif _ts == "save" :
                LibcloudCmds.catalogs.cbtool[credentials_list].ex_shutdown_node(instance)
            elif (_ts == "attached" or _ts == "resume") and _cs == "fail" :
                LibcloudCmds.catalogs.cbtool[credentials_list].ex_power_on_node(instance)
            elif (_ts == "attached" or _ts == "restore") and _cs == "save" :
                LibcloudCmds.catalogs.cbtool[credentials_list].ex_power_on_node(instance)

    def vmrunstate(self, obj_attr_list) :
        _status = 100
        _fmsg = "An error has occurred, but no error message was captured"
        try :
            _ts = obj_attr_list["target_state"]
            _cs = obj_attr_list["current_state"]

            _credentials_list = obj_attr_list["credentials_list"]
            self.connect(_credentials_list)

            _curr_tries = 0
            _wait = int(obj_attr_list["update_frequency"])
            self.common_messages("VM", obj_attr_list, "runstate altering", 0, '')

            firsttime = True
            _time_mark_rrs = int(time())
            _instance = False
            while True :
                _errmsg = "get_vm_instance"
                cbdebug("Getting instance...")
                _instance = self.get_vm_instance(obj_attr_list)

                _curr_tries += 1
                _msg = "Inside runstate: " + _errmsg
                _msg += " after " + str(_curr_tries) + " attempts. Will retry in " + str(_wait) + " seconds."
                cbdebug(_msg)

                if (_ts in ["fail", "save"] and _instance.state != NodeState.STOPPED) or (_ts in ["attached", "resume", "restore"] and _instance.state != NodeState.RUNNING) :
                    try :
                        if firsttime :
                            if "mgt_201_runstate_request_originated" not in obj_attr_list :
                                obj_attr_list["mgt_201_runstate_request_originated"] = _time_mark_rrs
                        self.vmrunstate_do(_instance, _credentials_list, obj_attr_list)
                        if firsttime :
                            obj_attr_list["mgt_202_runstate_request_sent"] = int(time()) - int(obj_attr_list["mgt_201_runstate_request_originated"])
                        firsttime = False
                    except Exception, e :
                        cbwarn(str(e), True)

                    cbdebug(self.get_description() + " request still not complete. Will try again momentarily...", True)
                    sleep(_wait)
                    continue

                break

            if "mgt_201_runstate_request_originated" not in obj_attr_list :
                obj_attr_list["mgt_201_runstate_request_originated"] = _time_mark_rrs

            if "mgt_202_runstate_request_sent" not in obj_attr_list :
                obj_attr_list["mgt_202_runstate_request_sent"] = int(time()) - int(obj_attr_list["mgt_201_runstate_request_originated"])

            _time_mark_rrc = int(time())
            obj_attr_list["mgt_203_runstate_request_completed"] = _time_mark_rrc - _time_mark_rrs

            _msg = "VM " + obj_attr_list["name"] + " runstate request completed."
            cbdebug(_msg)

            _status = 0

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            _status, _msg = self.common_messages("VM", obj_attr_list, "runstate altered", _status, _fmsg)
            return _status, _msg

    @trace
    def vmmigrate(self, obj_attr_list) :
        '''
        TBD
        '''
        return 0, "NOT SUPPORTED"

    @trace
    def vmresize(self, obj_attr_list) :
        '''
        TBD
        '''
        return 0, "NOT SUPPORTED"

    @trace
    def imgdelete(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _image_instance = False
            
            _fmsg = "An error has occurred, but no error message was captured"
            
            self.common_messages("IMG", obj_attr_list, "deleting", 0, '')

            _credentials_list = self.rotate_token(obj_attr_list["cloud_name"])

            obj_attr_list["credentials_list"] = _credentials_list

            self.connect(_credentials_list, obj_attr_list)

            _image_instance = self.get_images(obj_attr_list)
                
            if _image_instance :

                _x = obj_attr_list["imageid1"] 
                obj_attr_list["imageid1"] = _image_instance.id
                
                LibcloudCmds.catalogs.cbtool[_credentials_list].delete_image(_image_instance)

                _wait = int(obj_attr_list["update_frequency"])
                _curr_tries = 0
                _max_tries = int(obj_attr_list["update_attempts"])

                _image_deleted = False
                       
                while not _image_deleted and _curr_tries < _max_tries :

                    _image_instance = self.get_images(obj_attr_list, False)

                    if not _image_instance :
                        _image_deleted = True
                    else :
                        sleep(_wait)
                        _curr_tries += 1

                obj_attr_list["imageid1"] = _x
                        
            _status = 0

        except Exception, e :
            _status = 23
            _fmsg = str(e)
            
        finally :
            _status, _msg = self.common_messages("IMG", obj_attr_list, "deleted", _status, _fmsg)
            return _status, _msg

    @trace
    def aidefine(self, obj_attr_list, current_step) :
        '''
        TBD
        '''
        lock = False
        try :
            if current_step == "provision_originated" :
                credentials_list = self.rotate_token(obj_attr_list["cloud_name"])
                tenant = credentials_list.split(":")[0]
                obj_attr_list["tenant"] = tenant
                obj_attr_list["credentials_list"] = credentials_list
                self.osci.pending_object_set(obj_attr_list["cloud_name"], "AI", \
                    obj_attr_list["uuid"], "credentials_list", credentials_list)

                # Cache libcloud objects for this daemon / process before the VMs are attached
                self.connect(credentials_list, obj_attr_list)

            _fmsg = "An error has occurred, but no error message was captured"

            self.take_action_if_requested("AI", obj_attr_list, current_step)

            _status = 0

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if lock :
                self.unlock(obj_attr_list["cloud_name"], "AI", obj_attr_list["uuid"], lock)
            if _status :
                _msg = "AI " + obj_attr_list["name"] + " could not be defined "
                _msg += " on " + self.get_description() + " \"" + obj_attr_list["cloud_name"] + "\" : "
                _msg += _fmsg
                cberr(_msg)
                raise CldOpsException(_status, _msg)
            else :
                _msg = "AI " + obj_attr_list["uuid"] + " was successfully "
                _msg += "defined on " + self.get_description() + " \"" + obj_attr_list["cloud_name"]
                _msg += "\"."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def get_libcloud_driver(self, libcloud_driver, tenant, *credentials) :
            raise CldOpsException("You must override this function, please.", 4920)

    '''
        You can override this function to modify the 'extra' parameter attributes as you see fit
        during instance creation time.
        If parameters are added that are common to all libcloud adapters, please submit a pull
        request and update this file directly.
    '''
        
    @trace
    def pre_vmcreate(self, obj_attr_list, extra) :
        return extra

    @trace
    def get_real_driver(self, who) :
        return get_driver(getattr(Provider, who))

    @trace
    def get_my_driver(self, obj_attr_list) :
        return LibcloudCmds.catalogs.cbtool[obj_attr_list["credentials_list"]]

    @trace
    def repopulate_images(self, obj_attr_list) :
        LibcloudCmds.images = self.get_my_driver(obj_attr_list).list_images()

    @trace
    def repopulate_keys(self, obj_attr_list) :
        LibcloudCmds.keys[obj_attr_list["credentials_list"]] = self.get_my_driver(obj_attr_list).list_key_pairs()

    @trace
    def rotate_token(self, cloud_name) :
        '''
        TBD
        ''' 
        vmc_defaults = self.osci.get_object(cloud_name, "GLOBAL", False, "vmc_defaults", False)
        _credentials_lists = vmc_defaults["credentials"].split(",")
        lock = self.lock(cloud_name, "VMC", "shared_access_token_counter", "credentials_list")

        assert(lock)

        current_token = 0 if "current_token" not in vmc_defaults else int(vmc_defaults["current_token"])
        new_token = current_token

        if len(_credentials_lists) > 1 :
            new_token += 1
            if new_token == len(_credentials_lists) :
                new_token = 0

        self.osci.update_object_attribute(cloud_name, "GLOBAL", "vmc_defaults", False, "current_token", str(new_token))
        self.unlock(cloud_name, "VMC", "shared_access_token_counter", lock)

        return _credentials_lists[current_token]
